"""Step 3's container input, driven in a real browser against a running stack.

**A test that asserts a control exists does not assert anyone can use it.** This
repository has shipped a dialog whose submit button was invisible, a table whose
scrollbar was drawn 4,500px below the fold, a form that could not be submitted at
all and a statistics headline that stayed English while 301 tests passed. So
nothing here greps markup: a container is chosen from the real `<select>`, a
count is typed into the real input, and the assertion is on the mass that
actually reaches `POST /api/v1/calculate`.

**The number under test is `"139.200"`.** Two 240 L wheelie bins at the seeded
0.29 kg/L is 139.2 kg, and §6.2's whole safety property for this feature is that
it arrives as *exactly* the `qty_kg` a visitor who had typed 139.2 kilograms
would have sent — same string, same three decimals, same mass-conservation check
against the step-4 allocation. `test_the_request_body_is_what_a_typed_mass_would
_have_sent` runs both paths and compares the two request bodies.

**Two languages and two widths**, because this feature adds a `<select>` whose
options come from the database and a running total whose wording comes from a
catalogue. §7.7.7 rules that a staff-typed label is published exactly as written,
so the container names must be **identical** in Arabic and in English while
everything around them changes — a legend the other way round is the failure this
project keeps finding by looking at screenshots rather than at tests. Arabic is
also where a locale-aware number formatter would render Eastern Arabic numerals,
and `formatNumber` is pinned to `en-NZ` to stop it.

**Running these.** Playwright is not a project dependency, for the reason
`test_step_navigation.py` gives, and the stack must be at migration **0015** —
the presets this file names do not exist before it::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d
    pytest tests/web/test_container_input_browser.py

Point `KAICALC_WEB_URL` at a scratch stack when the one on :18080 is older; every
test here skips with the reason rather than failing, because a stack at 0013 is a
stale environment and not a defect in this branch.

**Mutation record.** Each was `docker cp`'d into the running web container and
the named test watched to fail. All thirteen were killed — but **four did not
start that way**, and what they exposed is written into the tests they now fail:

=====================================================  =====================================
Mutation                                               Killed by
=====================================================  =====================================
`entryTotal` returns `tonnes` for a container           `..._review_step_says_what_was_entered`
`containerTotalText` always returns `''`                `..._running_total_shows_the_mass`
the 10,000 bound is deleted                             `..._more_than_ten_thousand_is_refused`
the two-decimal rule is deleted from the count          `..._third_decimal_place_..._refused`
the option text becomes a catalogue string              `..._container_names_are_not[ar]`
the unit select stops pinning kilograms                 `..._tonnes_to_containers_..._kilograms`
the review prints kilograms instead of the container    `..._review_step_says_what_was_entered`
the select is re-sorted alphabetically by code          `..._offered_smallest_first`
the containers are left off the select entirely         `..._offered_smallest_first`
the `preset:` prefix is dropped from the value          `..._running_total_shows_the_mass`
the count is rounded to a whole container               `..._half_full_bin_is_half_the_mass`
the total uses a locale-aware formatter                 `..._digits_are_not[de]`
the fields are pinned behind the sticky bar             `..._reach_and_use_both_controls`
=====================================================  =====================================

The four that survived first time, and what changed:

1. **The tonnes mutant** was found by an assertion that scanned the whole
   `.content-section` for `"139.200 kg"` — and the *destinations* block two rows
   below prints "139.20 kilograms (139.200 kg)", so the substring was there
   whatever the waste-amount row said. `review_block()` now scopes it.
2. **The locale-aware formatter** survived in Arabic, because Chromium's bare
   `ar` renders Latin digits — the same way it survived earlier on this branch.
   German is now in the parametrisation; it renders `139,200` and the mutant
   dies.
3. **`t(preset.label)`** survived because `t()` falls back to its own key, so
   wrapping a label that no catalogue carries changes nothing. That is not a
   hole: a catalogue that *did* carry a container label is caught by
   `test_i18n_web.py::test_no_catalogue_carries_a_key_the_front_end_never_asks
   _for`, because an indirect `t(SOMETHING)` is invisible to the key extractor
   and the entry reads as stale. The mutation was replaced with one that does
   change behaviour.
4. **`totalUnit: target.value`** survived, and the mutation was the weak half of
   it: `unitLabel()` answers "kilograms" for anything that is not `tonnes`, so
   assigning the raw option value still *displayed* kilograms. The mutation that
   matters is `totalUnit: preset ? state.totalUnit : target.value` — keep
   whatever was there — which is the tonnes-then-container path and does kill
   `test_switching_from_tonnes_to_containers_leaves_step_four_in_kilograms`.
   A mutation can be behaviour-preserving; that says nothing about the test.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from decimal import Decimal

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the container input; it is unverified without it",
)

#: The ORIGIN, not a page. Two things are built from it and they are not the same
#: shape: the calculator's URL, and the API URL the `taxonomy` fixture reads. Folding
#: `/index.html` into this constant made the second one
#: `http://localhost:18080/index.html/api/v1/taxonomy`, which 404s - and because that
#: fixture *skips* on an unreachable taxonomy rather than failing, all twenty-four
#: assertions in this file went green as skips and measured nothing.
BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/")

#: The calculator's own URL. `/` serves this same file, and it is named anyway so
#: the constant does not move when the `index` directive does.
CALCULATOR = BASE + "/index.html"

#: The preset this file drives, and the mass it must produce. Held here rather
#: than read from the API so that a seed change has to be *noticed*: this test's
#: subject is the arithmetic reaching the wire, and a fixture that recomputed the
#: expected value from the same row it is checking would assert nothing.
PRESET_CODE = "wheelie_bin_240l"
PRESET_KG = Decimal("69.6000")
COUNT = "2"
TOTAL_KG = "139.200"

#: A second preset, used for the ordering assertion. 1100 L sorts *after* 660 L
#: by mass and *before* it alphabetically by code, which is the whole reason
#: `get_taxonomy` orders by `kg_per_unit`.
BIG_PRESET_CODE = "front_loader_1100l"

VIEWPORTS = [
    pytest.param(1278, 983, id="1278x983"),
    pytest.param(390, 700, id="390x700"),
]

FORCE_AUTO = "html { scroll-behavior: auto !important; }"

#: Distance in CSS px from the bottom of the element to the bottom of the
#: viewport at scroll top. Negative means reachable without scrolling.
PAST_FOLD = """
(sel) => {
  window.scrollTo(0, 0);
  const el = document.querySelector(sel);
  if (!el) return null;
  return Math.round(el.getBoundingClientRect().bottom - window.innerHeight);
}
"""

#: Is the element actually on the screen — laid out, non-zero, not clipped, and
#: not covered at its own centre by something else? `toBeVisible` answers the
#: first three and not the fourth, and the fourth is what the invisible submit
#: button and the below-the-fold scrollbar both were.
REALLY_VISIBLE = """
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return { found: false };
  el.scrollIntoView({ block: 'center' });
  const box = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  const at = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  return {
    found: true,
    width: Math.round(box.width),
    height: Math.round(box.height),
    display: style.display,
    visibility: style.visibility,
    covered: !(el === at || el.contains(at) || (at && at.contains(el))),
  };
}
"""


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

    The POST is fulfilled locally — `/api/v1/calculate` is rate limited and this
    file drives it repeatedly — but the **request body is kept**, because the
    body is this module's subject. `sent` is the list of parsed bodies.
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
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(RESULT_STUB),
            )

        page.route("**/api/v1/calculate*", capture)
        url = CALCULATOR + ("&" if "?" in CALCULATOR else "?") + f"lang={language}"
        try:
            page.goto(url, wait_until="networkidle", timeout=20000)
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


#: Enough of a §6.2 response to reach the results screen. This module asserts on
#: the request, never on the response, so the figures here are arbitrary.
RESULT_STUB = {
    "token": "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
    "factor_set": {"version_label": "MOCK-v0 — PLACEHOLDER", "is_mock": True},
    "factor_source": "published",
    "gwp_horizon": 100,
    "totals": {"total_kg": "139.200", "current": {"metrics": [], "equivalences": []}},
    "entries": [{"current": {"metrics": [], "equivalences": []}}],
}


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


def to_container_step(page, code=PRESET_CODE):
    """Walk to step 3 and choose a container on the unit `<select>`.

    **There is no separate mode control.** The containers are an `<optgroup>` on
    the select that already asks for the unit, and choosing one *is* switching
    the mode. The first build put a "By weight / By container" radio pair above
    the form instead; it cost 169px at 1278x983 on a step with 50px of headroom
    and broke three of `test_step_navigation.py`'s assertions — §7.6.3, that
    advancing must never require scrolling. `preset:` distinguishes a container
    code from `kilograms` and `tonnes` inside one value space.
    """
    to_amount_step(page)
    page.select_option("#total-unit", f"preset:{code}")
    page.wait_for_selector("#unit-count")
    return page


#: The container `<optgroup>`, which is always the last one on the select.
CONTAINER_OPTIONS = "#total-unit optgroup:last-of-type option"


def review_block(page, heading):
    """One `.review-block` of the review step, found by its own heading.

    Scoping matters more than it looks: the review step prints four blocks and
    three of them carry a mass, so an assertion made against the whole section
    can be satisfied by the wrong figure in the wrong row — which is exactly
    what happened to `test_the_review_step_says_what_was_entered_and_what_it
    _came_to` before this helper existed.
    """
    for block in page.query_selector_all(".review-block"):
        text = block.inner_text()
        if text.strip().startswith(heading):
            return text
    raise AssertionError(f"no review block headed {heading!r}")


def fill_container(page, code=PRESET_CODE, count=COUNT):
    if not page.query_selector("#unit-count"):
        page.select_option("#total-unit", f"preset:{code}")
        page.wait_for_selector("#unit-count")
    page.fill("#unit-count", count)
    page.wait_for_timeout(80)
    return page


# ---------------------------------------------------------------- the control


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_a_visitor_can_actually_reach_and_use_both_controls(page_at, width, height):
    """Laid out, non-zero, and not covered at their own centre.

    The step-navigation bar is `position: sticky; bottom: 0`, so it is the one
    element in this layout that can sit *on top of* a form control — which is
    the shape of the invisible-submit-button defect, arriving from the other
    direction.
    """
    page = to_container_step(page_at(width, height))
    for selector in ("#total-unit", "#unit-count"):
        seen = page.evaluate(REALLY_VISIBLE, selector)
        assert seen["found"], f"{selector} is not in the document at {width}x{height}"
        assert seen["width"] > 0 and seen["height"] >= 40, (selector, seen)
        assert seen["display"] != "none" and seen["visibility"] == "visible", (selector, seen)
        assert not seen["covered"], (
            f"{selector} is covered at its centre at {width}x{height} — a control "
            f"nobody can click: {seen}"
        )


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_the_running_total_is_on_screen_without_scrolling(page_at, width, height):
    """**The whole feature is this sentence, and on a phone it was hidden.**

    The running total first sat after the amount panel, which reads correctly on
    a desktop and put it behind `.step-nav` — `position: sticky; bottom: 0`, so
    always on screen — at 390x700. A visitor on a phone typed "2", and the
    confirmation that two wheelie bins is 139.200 kg was underneath the bar
    telling them to continue. Every assertion passed; the screenshot is what
    showed it.

    It is now the last child of the count's own field. **The measurement is taken
    with the input scrolled into view**, which is where a visitor typing into it
    necessarily is — at 390x700 the whole amount panel is below the fold at
    scroll top and always has been; §7.6.3's rule is about the *primary action*,
    and `test_continue_stays_reachable_without_scrolling` is what holds that.
    What this test holds is that the answer is next to the question: adjacent to
    the input, and clear of the bar at the moment the visitor can see the input
    at all.
    """
    page = fill_container(to_container_step(page_at(width, height)))
    measured = page.evaluate(
        """() => {
          const input = document.querySelector('#unit-count');
          const total = document.querySelector('#container-total');
          if (!total) return null;
          input.scrollIntoView({ block: 'center' });
          const i = input.getBoundingClientRect();
          const t = total.getBoundingClientRect();
          const b = document.querySelector('.step-nav').getBoundingClientRect();
          return {
            text: total.textContent.trim(),
            gap: Math.round(t.top - i.bottom),
            top: Math.round(t.top),
            bottom: Math.round(t.bottom),
            viewport: window.innerHeight,
            barTop: Math.round(b.top),
          };
        }"""
    )
    assert measured and measured["text"], f"no running total at {width}x{height}"
    # Adjacent to the input, not stranded past the unit select — which is where it
    # was, and is what put it behind the bar on a phone.
    assert 0 <= measured["gap"] <= 40, (
        f"the running total is {measured['gap']}px from the input it describes at "
        f"{width}x{height}: {measured}"
    )
    assert measured["top"] >= 0 and measured["bottom"] <= measured["viewport"], (
        f"the running total is off screen with the input centred at {width}x{height}: "
        f"{measured}"
    )
    assert measured["bottom"] <= measured["barTop"], (
        f"the running total is behind the sticky navigation bar at {width}x{height}: "
        f"{measured}"
    )


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_continue_stays_reachable_without_scrolling(page_at, width, height):
    """Container mode adds a fieldset and a running total above the bar. §7.6.3
    and `test_step_navigation.py`: advancing must never require scrolling."""
    page = fill_container(to_container_step(page_at(width, height)))
    past = page.evaluate(PAST_FOLD, '[data-action="continue"]')
    assert past is not None and past <= 0, (
        f"Continue sits {past}px past the fold at {width}x{height} in container mode"
    )


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_the_page_does_not_scroll_sideways(page_at, width, height):
    """§7.6.8, measured rather than assumed. The longest option text is a
    staff-typed label, so it is exactly the kind of string that has pushed this
    layout wide before."""
    page = fill_container(to_container_step(page_at(width, height)))
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"the body scrolls {overflow}px sideways at {width}x{height}"


def test_the_containers_are_offered_smallest_first(page_at, taxonomy):
    """`get_taxonomy` orders by `kg_per_unit` and the form renders that order as
    given. Alphabetically by code, `front_loader_1100l` precedes
    `front_loader_660l` — this asserts it does not."""
    page = to_amount_step(page_at(1278, 983))
    codes = page.eval_on_selector_all(
        "#total-unit optgroup:last-of-type option",
        "nodes => nodes.map(n => n.value.replace(/^preset:/, ''))",
    )
    served = [row["code"] for row in taxonomy["unit_presets"]]
    assert codes == served
    assert codes.index(BIG_PRESET_CODE) > codes.index("front_loader_660l")


# ------------------------------------------------------------- the arithmetic


def test_the_running_total_shows_the_mass_the_count_comes_to(page_at):
    """Two 240 L bins at 69.6000 kg each. The digits are read off the rendered
    element, not off a call."""
    page = fill_container(to_container_step(page_at(1278, 983)))
    assert Decimal(COUNT) * PRESET_KG == Decimal("139.2000")
    total = page.inner_text("#container-total")
    assert TOTAL_KG in total, total
    assert "kg" in total, total


def test_a_half_full_bin_is_half_the_mass(page_at):
    """"Half a wheelie bin" is the case the two-decimal count rule exists for."""
    page = fill_container(to_container_step(page_at(1278, 983)), count="0.5")
    expected = f"{(Decimal('0.5') * PRESET_KG):.3f}"
    assert expected == "34.800"
    assert expected in page.inner_text("#container-total")


def test_the_total_that_reaches_step_four_is_that_mass(page_at):
    """Step 4 allocates the step-3 total, and §6.2 compares the two. The summary
    is read off the screen the visitor is looking at."""
    page = fill_container(to_container_step(page_at(1278, 983)))
    page.click('[data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')
    summary = page.inner_text("#current-summary")
    assert "139.20" in summary, summary
    # The rows are in kilograms, never in bins: a destination amount is a mass.
    assert "kilograms" in summary, summary


def test_switching_from_tonnes_to_containers_leaves_step_four_in_kilograms(page_at):
    """**The transition, which is where this could go wrong silently.**

    `state.totalUnit` is the unit step 4 allocates in, and it is *not* reset by
    the mode radio unless something resets it. A visitor who picks tonnes, then
    changes their mind and describes their bins instead, would otherwise reach a
    step 4 whose rows say "tonnes" against a total of 139.2 **kilograms** — a
    thousandfold error, on a screen that looks entirely normal, refused by
    nothing because the numbers are individually plausible.

    Every other assertion in this file starts in container mode and so never
    exercises this path; the default is already kilograms, which is what makes
    the defect invisible to them.
    """
    page = page_at(1278, 983)
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(60)
    page.click('[data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    page.click('[data-action="continue"]')
    page.wait_for_selector("#total-unit")
    page.select_option("#total-unit", "tonnes")
    page.wait_for_timeout(80)
    page.fill("#total-waste", "5")
    page.select_option("#total-unit", f"preset:{PRESET_CODE}")
    page.wait_for_selector("#unit-count")
    fill_container(page)
    page.click('[data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')

    summary = page.inner_text("#current-summary")
    assert "kilograms" in summary, summary
    assert "tonnes" not in summary, (
        "step 4 is still allocating in tonnes against a kilogram total: " + summary
    )
    assert "139.20" in summary, summary
    row_label = page.get_attribute('[data-line-field="amount"] >> nth=0', "aria-label")
    assert "kilograms" in row_label, row_label


def test_the_review_step_says_what_was_entered_and_what_it_came_to(page_at, taxonomy):
    """"2.00 × 240 L wheelie bin (full)" and "139.200 kg", in that order.

    The count is what a reader can check against their own bins, so it is not
    replaced by the kilograms — it is followed by them.

    **Read out of the waste-amount block, not out of the section.** The first
    version of this test scanned the whole `.content-section` for
    `"139.200 kg"`, and passed under a mutation that made the review print
    *139,200.000 kg* — because the **destinations** block two rows below prints
    "139.20 kilograms (139.200 kg)", and the substring was found there. That is
    this repository's most common failure mode, caught here by mutation rather
    than by reading.
    """
    page = fill_container(to_container_step(page_at(1278, 983)))
    page.click('[data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')
    page.fill('[data-line-field="amount"] >> nth=0', "139.2")
    page.wait_for_timeout(80)
    page.click('[data-action="continue"]')
    page.wait_for_selector('[data-action="calculate"]')
    label = next(
        row["label"] for row in taxonomy["unit_presets"] if row["code"] == PRESET_CODE
    )
    block = review_block(page, "Waste amount")
    assert label in block, block
    assert f"{COUNT}.00" in block, block
    assert f"{TOTAL_KG} kg" in block, block
    # The thousands separator would be the tell for a tonnes/kilograms slip:
    # 139.2 tonnes reads as "139,200.000 kg" and still contains no wrong word.
    assert "139,200" not in block, block


def test_the_request_body_is_what_a_typed_mass_would_have_sent(page_at):
    """**The safety property of the whole feature**, asserted on the wire.

    Two 240 L wheelie bins and "139.2 kilograms" are the same submission. If they
    are not, a container entry is a second way of producing a `qty_kg` — with its
    own rounding, its own decimal places and its own bugs — and §6.2's
    mass-conservation check is comparing two numbers that were derived
    differently.
    """
    page = fill_container(to_container_step(page_at(1278, 983)))
    page.click('[data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')
    page.fill('[data-line-field="amount"] >> nth=0', "139.2")
    page.wait_for_timeout(80)
    page.click('[data-action="continue"]')
    page.wait_for_selector('[data-action="calculate"]')
    page.click('[data-action="calculate"]')
    page.wait_for_timeout(600)
    assert page.sent, "no calculate request was made"
    from_container = page.sent[-1]

    typed = page_at(1278, 983)
    typed.wait_for_selector('input[name="sector"]')
    typed.evaluate("document.querySelector('input[name=sector]').click()")
    typed.wait_for_timeout(60)
    typed.click('[data-action="continue"]')
    typed.wait_for_selector('input[name="food-category"]')
    typed.click('[data-action="continue"]')
    typed.wait_for_selector("#total-waste")
    typed.fill("#total-waste", "139.2")
    typed.click('[data-action="continue"]')
    typed.wait_for_selector('[data-line-field="amount"]')
    typed.fill('[data-line-field="amount"] >> nth=0', "139.2")
    typed.wait_for_timeout(80)
    typed.click('[data-action="continue"]')
    typed.wait_for_selector('[data-action="calculate"]')
    typed.click('[data-action="calculate"]')
    typed.wait_for_timeout(600)
    assert typed.sent, "no calculate request was made on the typed-mass path"

    def lines(body):
        return [entry["current"] for entry in body["entries"]]

    assert lines(from_container) == lines(typed.sent[-1])
    assert lines(from_container)[0][0]["qty_kg"] == TOTAL_KG


def test_a_count_of_more_than_ten_thousand_is_refused(page_at):
    """The bound, on the screen. Half a wheelie bin is a reasonable thing to
    say; two hundred and forty thousand of them is not."""
    page = fill_container(to_container_step(page_at(1278, 983)), count="10001")
    page.click('[data-action="continue"]')
    page.wait_for_timeout(150)
    assert page.query_selector("#unit-count"), "the step advanced on a refused count"
    assert "10,000" in page.inner_text(".content-section")


def test_a_third_decimal_place_in_the_count_is_refused(page_at):
    """The two-decimal rule follows the count, which is what somebody types —
    not the derived total, which has three because §6.2 accepts three."""
    page = fill_container(to_container_step(page_at(1278, 983)), count="1.234")
    page.click('[data-action="continue"]')
    page.wait_for_timeout(150)
    assert page.query_selector("#unit-count"), "the step advanced on a refused count"


# ------------------------------------------------------------- the languages


@pytest.mark.parametrize("language", ["ar", "zh"])
def test_the_interface_is_translated_and_the_container_names_are_not(page_at, taxonomy, language):
    """§7.7.7's ruling of 14 August, on the one screen that puts a staff-typed
    label and a catalogue string side by side.

    A translated container name would be the *natural* thing to expect and is
    the defect: `unit_preset.label` is edited in the panel, and the panel has no
    catalogue to edit it in.
    """
    page = to_amount_step(page_at(1278, 983, language=language))
    english = to_amount_step(page_at(1278, 983, language="en"))

    served = [row["label"] for row in taxonomy["unit_presets"]]
    options = page.eval_on_selector_all(
        "#total-unit optgroup:last-of-type option",
        "nodes => nodes.map(n => n.textContent)",
    )
    assert options == served, (
        f"a container label changed in {language}; §7.7.7 publishes it as written"
    )

    # The `<optgroup>` labels around them are interface strings and must change.
    groups = page.eval_on_selector_all(
        "#total-unit optgroup", "nodes => nodes.map(n => n.label)"
    )
    assert groups != english.eval_on_selector_all(
        "#total-unit optgroup", "nodes => nodes.map(n => n.label)"
    ), f"the option-group labels are still English in {language}"
    assert page.get_attribute("html", "lang") == language


@pytest.mark.parametrize("language", ["ar", "zh", "de"])
def test_the_running_total_is_translated_and_its_digits_are_not(page_at, language):
    """§7.7.7: the interface is translated, the figures are not localised.

    **German is in this list because Arabic cannot catch the defect and this
    branch has already lost a mutant to that.** `formatNumber` is pinned to
    `en-NZ`; replace the pin with the page's own language and Chromium's bare
    `ar` still renders Latin digits, so an Eastern-Arabic-digit check goes green
    against an unpinned formatter. German renders 139.2 as **`139,200`** — the
    decimal separator and the thousands separator swapped — so the exact string
    `139.200` is present in every language only while the pin is there. That is
    not cosmetic: `139,200` and `139.200` are the same figure written two ways,
    on a page whose whole subject is a number.

    The Eastern-Arabic check stays alongside it. It documents the other half of
    the rule and costs nothing; it is simply not the assertion with the teeth.
    """
    page = fill_container(to_container_step(page_at(1278, 983, language=language)))
    english = fill_container(to_container_step(page_at(1278, 983, language="en")))
    total = page.inner_text("#container-total")
    assert TOTAL_KG in total, total
    assert not re.search(r"[٠-٩۰-۹]", total), total
    assert total != english.inner_text("#container-total"), (
        f"the running total is still English in {language}"
    )


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_arabic_lays_the_step_out_right_to_left_without_overflowing(page_at, width, height):
    page = fill_container(to_container_step(page_at(width, height, language="ar")))
    assert page.get_attribute("html", "dir") == "rtl"
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"Arabic scrolls {overflow}px sideways at {width}x{height}"
    past = page.evaluate(PAST_FOLD, '[data-action="continue"]')
    assert past is not None and past <= 0, f"Continue is {past}px past the fold in Arabic"
