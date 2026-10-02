"""The step navigation bar is *reachable*, not merely present.

A test that asserts an element exists does not assert anyone can reach it, and
this repository has six defects on record that reached the owner through green
markup tests — a dialog whose submit button was invisible, a table whose
scrollbar was drawn 4,500px below the fold. So every assertion here is a
geometry measured in a real browser against the running stack.

**What is being defended.** The client's first look at the calculator was that
advancing requires scrolling at every step. Measured at 1278x983 before the
change, the primary action sat +2961px past the fold on the food-type step,
+796 on destinations, +630 on review and +1230 on results; at 938x898,
+3046 / +899 / +715 / +1342; at 390x700, five of the seven screens failed.
`stepNav` (`web/js/view.js`) pins that row with `position: sticky; bottom: 0`.
The goal is not zero scrolling — the food-type step is 40 categories long and
always will be — it is that **advancing never requires scrolling**.

**`html { scroll-behavior: smooth }` is forced to `auto` before every
measurement.** `scrollTo(0, 0)` followed immediately by `getBoundingClientRect`
otherwise returns mid-animation values; that made one earlier measurement pass
optimistic by up to 800px.

**Running these.** Playwright is not a project dependency — it is not in
`requirements.txt` and not in the `dev` extra, because it needs a browser
download that a marker installing this package should not be made to take. The
whole module skips without it, the same way `test_entry_destinations.py` skips
without Node::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d
    pytest tests/web/test_step_navigation.py

**Mutation hook.** `KAICALC_MUTATION_CSS` is injected as a stylesheet after
load. It exists so each rule these tests depend on can be knocked out and the
test watched to fail — the project's standard, because a rule no test kills is
a rule no test is really checking. See the table in the module docstring of the
commit that added this file.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from tests.web.steps import press_continue


from api.schemas import CalculatePayload

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure where the primary action lands; the bar is unverified without it",
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "calculate_response_single.json").read_text(encoding="utf-8"))
#: `/index.html` rather than `/`, and they are now the same document: nginx says
#: `index index.html` again. Named explicitly so this file measures the calculator
#: whatever the `index` directive says next.
BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080/index.html")


def _english(url: str) -> str:
    """`?lang=en`, the one-request override, so this file measures English.

    Belt and braces beside `locale="en-NZ"`: the override is what a support
    request or a screenshot uses, and it is the guarantee that does not depend
    on Chromium honouring a context locale in `navigator.languages`.
    """
    return url + ("&" if "?" in url else "?") + "lang=en"
MUTATION_CSS = os.environ.get("KAICALC_MUTATION_CSS", "")

#: The owner's two machines, both high-resolution panels at the OS default
#: scaling, plus a phone. The device scale factors are theirs; the CSS viewport
#: is what the arithmetic is done in.
VIEWPORTS = [
    pytest.param(1278, 983, 1.25, id="1278x983"),
    pytest.param(938, 898, 1.5, id="938x898"),
    pytest.param(390, 700, 3.0, id="390x700"),
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


@pytest.fixture
def page_at(browser):
    """A page at a given viewport, with the calculate POST fulfilled locally.

    `/api/v1/calculate` carrying `X-Dry-Run` is staff-only and answers 401, so
    the results view is reached by fulfilling the POST in-browser from the
    contract fixture — the same way both measurement passes reached it.
    """
    contexts = []

    def open_page(width, height, dpr=1.0):
        # `locale="en-NZ"` and the `?lang=en` below both pin the language, and
        # both are needed. Since v1.25 the calculator negotiates from
        # `navigator.languages`, so a machine whose browser prefers another
        # language renders this page in it and every English string asserted
        # below stops matching - which is exactly what happened on the
        # repository owner's machine the first time this ran after the
        # twenty catalogues landed. This file measures LAYOUT against the
        # English copy it was calibrated on; the language itself is
        # tests/web/test_i18n_browser.py's subject.
        # **`bypass_csp` is what lets this file measure at all, and it is a
        # statement about the harness rather than about the page.** D's
        # `location /` Content-Security-Policy sets `style-src 'self'`, which
        # is correct — the front end loads exactly one stylesheet from this
        # origin — and a `<style>` element created by `add_style_tag` is
        # precisely what that directive refuses. Both injections below are
        # instrumentation: `FORCE_AUTO` stops `scroll-behavior: smooth`
        # returning mid-animation geometry, and `KAICALC_MUTATION_CSS` is the
        # hook that lets each rule this file depends on be knocked out and the
        # test watched to fail. Without the bypass every test in this module
        # errors on the injection and measures nothing.
        #
        # **The CSP is not thereby untested.** It is asserted as a header by
        # `tests/test_d_statistics_content.py`, and enforced in a real browser
        # — with no bypass — by
        # `tests/web/test_csp.py::test_no_public_page_violates_its_own_policy`,
        # which is the test that would catch a directive that breaks the page.
        ctx = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=dpr,
            locale="en-NZ",
            bypass_csp=True,
        )
        contexts.append(ctx)
        page = ctx.new_page()
        page.route(
            "**/api/v1/calculate*",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(FIXTURE)),
        )
        try:
            page.goto(_english(BASE), wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {BASE}: {error}")
        page.add_style_tag(content=FORCE_AUTO)
        if MUTATION_CSS:
            page.add_style_tag(content=MUTATION_CSS)
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        return page

    yield open_page
    for ctx in contexts:
        ctx.close()


#: Playwright's `locale=` context option and the `?lang=` one-request override
#: (see `_english` above) both have to agree, for the same reason `page_at`
#: pins both to English - a mismatch lets the browser's own negotiation win.
#: `ar` is the one RTL language this project ships, and both are exercised at
#: the same five widths `page_at`'s own tests use so a layout finding
#: (clipping, in particular) is measured against the same breakpoints in
#: every direction and every script this file cares about.
#:
#: `en` is here so that a test which has to compare one measurement **across**
#: languages can take all of them from one fixture. `page_at_locale(w, h, dpr,
#: "en")` and `page_at(w, h, dpr)` are then the same page: `en-NZ` is the locale
#: `page_at` pins, and `?lang=en` is the override `_english` appends.
#:
#: `ru` is here because it held the **longest** label of the twenty catalogues
#: on the one control measured by `_SELECT_ARROW_PX`'s test below - "Единица
#: измерения" for `Unit`, seventeen characters against German's eleven - so a
#: control-width assertion that stopped at German would have stopped one language
#: short of its own worst case.
#:
#: **Since #74 that control's worst case is not in any catalogue**, and `ru` is
#: kept for a different reason. The one unit control offers every `unit_preset`
#: container, and a container's label is staff text printed verbatim (§7.7.7), so
#: the widest option - "23 L kerbside food scraps bin (full)", 254px of text - is
#: the same width in all twenty. What still varies by language is the *label*
#: beside the control, which is what decides whether the flex line wraps, and
#: Russian's 144px label is still the one that forces the wrap at 320px. So the
#: three-language matrix measures the wrap in the language where it happens and
#: the clip where it is deepest, which is everywhere.
#:
#: Adding rows does not reach any existing test: every one of them names its
#: languages explicitly in its own `parametrize`, so the reason the two fixtures
#: are separate stands unchanged - nothing here silently defaults.
_CONTEXT_LOCALE = {"en": "en-NZ", "de": "de-DE", "ar": "ar-SA", "ru": "ru-RU"}


@pytest.fixture
def page_at_locale(browser):
    """`page_at`, parameterised by language rather than pinned to English.

    A second fixture rather than an optional parameter on `page_at` itself -
    every other test in this file calls `page_at(width, height, dpr)` and
    must keep measuring the English copy it was calibrated against; adding a
    silently-defaulted fourth argument there is exactly the kind of change
    that would make a future English-only test pass in some other language by
    a typo, undetected until it started failing on layout it never touched.
    """
    contexts = []

    def open_page(width, height, dpr, lang):
        ctx = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=dpr,
            locale=_CONTEXT_LOCALE[lang],
            bypass_csp=True,
        )
        contexts.append(ctx)
        page = ctx.new_page()
        page.route(
            "**/api/v1/calculate*",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(FIXTURE)),
        )
        url = BASE + ("&" if "?" in BASE else "?") + f"lang={lang}"
        try:
            page.goto(url, wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {BASE}: {error}")
        page.add_style_tag(content=FORCE_AUTO)
        if MUTATION_CSS:
            page.add_style_tag(content=MUTATION_CSS)
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        return page

    yield open_page
    for ctx in contexts:
        ctx.close()


def walk(page):
    """Drive the wizard as a visitor would, yielding the step index *on arrival*
    at each screen — intro, 0..4, then results (5), plus `1.5` for step 2.5.

    **`1.5` is a screen, not a `state.step` value.** Step 2.5 is step 2's second
    panel and the step number does not move for it (`docs/interfaces.md` §7.3a), so
    there is no index of its own to yield; 1.5 says where the screen sits in the
    sequence, between `state.step` 1 and 2, and keeps this generator's output in
    the order the visitor meets it. It is yielded only where the panel is actually
    reachable.

    **The intro screen is back and is walked again.** It is step -1: a hero with its
    own "Start calculator" button, which every visitor meets because nginx serves
    `index.html` at `/`. It was deleted for a week while `home.html` held that
    address; `home.html` is retired, so the screen and this yield came back with it —
    seven screens to measure rather than six.

    A generator rather than a `go_to(step)` because the wizard is a sequence:
    re-walking it once per screen measures the same seven screens seven times
    over, and the visitor only walks it once.

    Each screen is yielded before it is interacted with, because that is the
    state the visitor lands in and the moment they look for the next action.
    """
    yield -1
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    yield 0
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    yield 1
    #: **Step 2.5 as an excursion, taken at its tallest and leaving no trace.**
    #:
    #: The panel only exists once a chosen category has foods, so reaching it means
    #: ticking, and ticking changes what step 3 renders -- a named leaf instead of
    #: the category-less one. Twenty-odd tests below measure step 3's layout through
    #: `advance_to(page, 2)`, and none of them are about a named leaf. So the walk
    #: goes in, is measured, and comes back out through the panel's own Back and the
    #: form's own *Clear all selections*: what it hands to `yield 2` is exactly the
    #: screen it handed before this excursion existed.
    #:
    #: **Every category, not one.** The panel's height is the number of groups on it,
    #: and one group is not the screen worth measuring -- a visitor who ticks ten
    #: categories gets ten groups and twenty checkboxes, which is the tallest this
    #: screen gets on the seeded vocabulary and the one that can put Continue past
    #: the fold. `__unspecified__` is left alone: it is a control value, never a
    #: category (`UNSPECIFIED_CHOICE` in `calculator.js`), and it adds no group.
    codes = page.evaluate(
        "() => [...document.querySelectorAll('input[name=food-category]')]"
        ".map(e => e.value).filter(v => v !== '__unspecified__')"
    )
    for code in codes:
        page.click(f'input[name="food-category"][value="{code}"]')
        page.wait_for_timeout(35)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_timeout(150)
    #: **The excursion has two ways in and one way out, and that is the fix.**
    #:
    #: Step 2.5 is yielded only where the panel is actually on screen -- absent
    #: wherever the published set does not release the item level, where the panel
    #: is correctly unreachable and there is nothing to measure. Asked of the DOM
    #: rather than of the taxonomy for `tests/web/steps.py`'s reason.
    #:
    #: **The untick, though, is unconditional**, and it was not. The ten ticks
    #: above happen whatever the flag says; only the panel does not. An earlier
    #: revision cleared them *inside* this branch, so on a stack whose published
    #: set has `item_level_enabled = 0` -- which is what set 15, published
    #: 2026-09-23, actually is -- the press above landed straight on step 3 with
    #: all ten categories still ticked, step 3 rendered ten suffixed leaves
    #: (`total-waste--standard-mix`, `total-waste--fruit`, ...), and the
    #: `#total-waste` below waited out thirty seconds against a screen that was
    #: working correctly. Eighty-nine of this file's hundred-and-one tests failed
    #: that way, and not one of them is about a named leaf. The release flag was
    #: masking a defect in this generator, not causing one.
    #:
    #: So both paths back out to the category step and clear it there: what
    #: `yield 2` is handed is the single-leaf screen twenty-odd tests below were
    #: calibrated on, with the flag off and with it on alike.
    if page.locator("#item-title").count():
        yield 1.5
        page.click('.step-nav [data-action="back-to-categories"]')
    else:
        #: Step 3's own Back (`stepNav({step: 2, back: backTarget(2)})`, and
        #: `backTarget(2)` is 1 on a plain forward walk with no review marker
        #: set), which is the way the visitor would return here too.
        page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('input[name="food-category"]')
    page.click('[data-action="clear-food"]')
    page.wait_for_timeout(120)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_timeout(150)
    page.wait_for_selector("#total-waste")
    yield 2
    page.fill("#total-waste", "1000")
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    yield 3
    page.fill('[data-line-field="amount"] >> nth=0', "1000")
    page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('[data-action="calculate"]')
    yield 4
    page.click('[data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=15000)
    page.wait_for_timeout(200)
    yield 5


def advance_to(page, step):
    """The single-screen form of `walk`, for the tests that measure one step."""
    for arrived in walk(page):
        if arrived == step:
            return page
    raise AssertionError(f"step {step} was never reached")


#: The screen, and the selector for the action that advances it. The intro has
#: no bar — it is a full-bleed hero whose own CTA measured -447 / -362 / -273 on
#: the pass that put this table here.
#:
#: **Step 5 changed under the plan of 2026-08-31 (Task 3).** The download used to
#: be `.step-nav [data-action="download-results"]`, pinned exactly like every
#: other step's advancing action. The client's second-round feedback was that
#: the text export was unreachable beside the PDF button, which lived outside
#: the bar in `.result-actions` — so the text download moved out to sit beside
#: it as an equal-weight pair, and the step-nav's primary slot for this one step
#: is now empty (`stepNav({..., action: null})`, `web/js/view.js`). Results is
#: the wizard's terminal screen — there is nothing further to "advance" to — so
#: what this table now measures there is the one action Task 3's own brief says
#: must stay put: the back action, still pinned in the bar.
PRIMARY = {
    -1: '[data-action="start"]',
    0: '.step-nav [data-action="continue"]',
    1: '.step-nav [data-action="continue"]',
    #: Step 2.5. The panel is the one screen whose height is set by how much the
    #: visitor ticked rather than by the layout, which is why `walk` measures it
    #: with every category chosen.
    1.5: '.step-nav [data-action="continue"]',
    2: '.step-nav [data-action="continue"]',
    3: '.step-nav [data-action="continue"]',
    4: '.step-nav [data-action="calculate"]',
    5: '.step-nav [data-action="go-step"]',
}


#: The back action for each screen, which is not the same selector everywhere.
#: Step 2.5's Back moves WITHIN step 2 rather than between two steps, so
#: `stepNav` renders it as `back-to-categories` with no `data-step` — a step
#: number there would be read by `goToStep`, which resets the panel, and the
#: button would do nothing visible (`web/js/view.js`). Measured here because the
#: bar is the same bar and a panel tall enough to push Continue past the fold
#: takes Back with it.
BACK = {1.5: '.step-nav [data-action="back-to-categories"]'}
BACK_DEFAULT = '.step-nav [data-action="go-step"]'


@pytest.mark.parametrize("width,height,dpr", VIEWPORTS)
def test_the_primary_action_of_every_step_is_reachable_without_scrolling(page_at, width, height, dpr):
    """The whole point. Every failure is collected rather than raised on the
    first, so one run names every screen that regressed instead of the earliest
    one."""
    page = page_at(width, height, dpr)
    failures = []
    for step in walk(page):
        page.wait_for_timeout(100)
        past = page.evaluate(PAST_FOLD, PRIMARY[step])
        if past is None:
            failures.append(f"step {step}: {PRIMARY[step]} is not on the page at all")
        elif past > 0:
            failures.append(f"step {step}: primary action is {past}px past the fold")
    assert not failures, "; ".join(failures)


def test_the_walk_actually_reaches_the_food_panel(page_at):
    """**The two sweeps above are conditional on a screen existing, so this says
    it does.**

    `walk` yields 1.5 only when the panel is on screen, which is right -- a
    deployment whose set does not release the item level has no such screen and
    nothing to measure. But it means both viewport sweeps stay green on a stack
    where the panel never opens at all, whether that is a correct absence or a
    landing that regressed, and green would be the same either way.

    This asserts the screen was reached, on a stack that releases it, and reports
    the two conditions apart so the skip cannot hide the regression.
    """
    page = page_at(1278, 983, 1.25)
    reached = [step for step in walk(page)]
    if 1.5 not in reached:
        released = page.evaluate(
            "() => fetch('/api/v1/taxonomy').then(r => r.json())"
            ".then(t => Boolean(t.factor_set && t.factor_set.item_level_enabled))"
        )
        if not released:
            pytest.skip(
                "the published factor set has item_level_enabled false, so step 2.5 "
                "is correctly absent; seed a fresh database to measure it"
            )
        raise AssertionError(
            "the set releases the item level but `walk` never reached step 2.5 -- "
            f"it visited {reached}, so both viewport sweeps above measured six "
            "screens and said nothing about the panel"
        )
    #: The panel is worth measuring because of what is ON it, and a panel that
    #: opened with no groups would satisfy the yield above while measuring a
    #: screen no visitor sees.
    assert reached.index(1.5) == reached.index(1) + 1, reached


def _set_item_level(page, released: bool) -> None:
    """Force `GET /taxonomy`'s `factor_set.item_level_enabled`, in the browser.

    Instrumentation of the same kind `page_at` already installs for the calculate
    POST, and for the same reason: the screen this file has to measure is a
    property of the **deployment's published factor set**, not of the test. Set
    15, published 2026-09-23, has the flag off and carries no item-level factor
    row; set 14, which has both, is archived. So on this stack step 2.5 is
    unreachable, and every assertion about `walk`'s behaviour with the panel
    *open* would be made by no test at all.

    Only the one boolean is substituted. The response is fetched from the real
    API and everything else on it -- the forty-seven foods, the nine categories,
    their parents and their order -- is the deployment's own, so the panel that
    opens is built from the real vocabulary by the real `itemStep`.

    Routes outlive a navigation, so the caller reloads after calling this.
    """

    def handler(route):
        body = route.fetch().json()
        body.setdefault("factor_set", {})["item_level_enabled"] = released
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        )

    page.route("**/api/v1/taxonomy*", handler)


#: Every `id` step 3 gives an amount field. One leaf keeps the bare `total-waste`;
#: more than one suffixes each with its own leaf slug (`fieldId`, `calculator.js`),
#: which is the difference this file's own `wait_for_selector("#total-waste")`
#: turns on.
AMOUNT_FIELD_IDS = """
() => [...document.querySelectorAll('[id^="total-waste"]')].map(e => e.id)
"""


@pytest.mark.parametrize("released", [False, True], ids=["flag-off", "flag-on"])
def test_the_walk_hands_step_three_one_leaf_on_either_side_of_the_release_flag(page_at, released):
    """`walk` must hand `yield 2` the same screen whether step 2.5 was reachable.

    **This is a test defect it was written for, not a product one.** `walk` ticks
    every food category to measure step 2.5 at its tallest, and an earlier
    revision cleared them again only *inside* the branch that entered the panel.
    With `item_level_enabled` off on the published set the branch is skipped, the
    ten ticks survive, step 3 renders ten leaves whose amount fields are suffixed
    (`total-waste--standard-mix`, `total-waste--fruit`, ...), and `walk`'s own
    `wait_for_selector("#total-waste")` waits out thirty seconds against a screen
    that is behaving correctly. Eighty-nine of this file's hundred-and-one tests
    failed exactly there, and none of them is about a named leaf.

    Both states are forced rather than read, so this holds on a deployment whose
    set releases the item level and on one that does not -- and the two are not a
    hypothetical: set 14 released it, set 15 does not, and the flag moved under
    this file without a line of it changing.
    """
    page = page_at(1278, 983, 1.25)
    _set_item_level(page, released)
    page.goto(_english(BASE), wait_until="networkidle", timeout=15000)
    page.wait_for_selector('[data-action="start"]', timeout=10000)

    ids_at_step_three = None
    reached = []
    for step in walk(page):
        reached.append(step)
        if step == 2:
            ids_at_step_three = page.evaluate(AMOUNT_FIELD_IDS)

    #: The panel is reachable exactly when the flag releases it, so it is the one
    #: thing that legitimately differs between the two runs.
    assert (1.5 in reached) is released, reached
    assert [step for step in reached if step != 1.5] == [-1, 0, 1, 2, 3, 4, 5], reached
    #: And this is what must NOT differ: one leaf, and the singleton id every
    #: other test in this file is written against.
    assert ids_at_step_three == ["total-waste"], ids_at_step_three


@pytest.mark.parametrize("width,height,dpr", VIEWPORTS)
def test_the_back_action_of_every_step_is_reachable_without_scrolling(page_at, width, height, dpr):
    """Back rides in the same bar, so it is the same guarantee — but it is
    asserted separately because a grid that overflows puts one of the two out
    of reach without touching the other."""
    page = page_at(width, height, dpr)
    failures = []
    for step in walk(page):
        # The intro screen has no step bar at all - it is a full-bleed hero - so
        # there is no Back to measure on it.
        if step == -1:
            continue
        # Step one HAS a Back again, and it goes to the intro. It lost it for the
        # week the intro was deleted; asserted rather than merely walked, because
        # `go-step` with `back: null` renders no button and the loop below would
        # then report "step 0 has no Back action" without saying why.
        if step == 0:
            assert page.query_selector('.step-nav [data-action="go-step"]') is not None, (
                "step one has no Back button; it should return to the introduction"
            )
        page.wait_for_timeout(100)
        past = page.evaluate(PAST_FOLD, BACK.get(step, BACK_DEFAULT))
        if past is None:
            failures.append(f"step {step} has no Back action in the bar")
        elif past > 0:
            failures.append(f"step {step}: Back is {past}px past the fold")
    assert not failures, "; ".join(failures)


def test_the_results_step_bar_has_no_primary_button(page_at):
    """Task 3 gave `stepNav`'s `action` parameter a `null` case so the results
    step's bar can omit the primary button entirely (`web/js/view.js`) — its
    "download" action moved out to sit beside the PDF button instead. Neither
    `test_results_export.py` nor the table above notices if that branch is
    ever removed: `PRIMARY[5]` and both viewport sweeps above only assert
    where the *back* action lands, and a `stepNav` that fell back to
    rendering `data-action="continue"` here — a dead button with no step left
    to continue to — would leave both green. This is the assertion that
    actually counts what the bar's primary slot holds on that one step.
    """
    page = advance_to(page_at(1278, 983, 1.0), 5)
    count = page.locator(".step-nav .button-primary").count()
    assert count == 0, (
        f"the results step-nav renders {count} primary button(s); its "
        "primary slot should be empty (`stepNav({action: null})`)"
    )


def test_the_bar_is_sticky_and_not_fixed(page_at):
    """`position` alone does not prove `sticky` over `fixed` - both report a
    `position` string, and a single gap measurement at one scroll offset is
    only ever one sample of a number this test has no business treating as a
    constant. What actually tells the two apart is motion: a `fixed` bar's
    distance from the fold cannot change, because `position: fixed` takes it
    out of the document being scrolled entirely, however tall that document
    grows. A `sticky`, in-flow bar's distance from the fold *grows* as the
    page scrolls, because the bar rides up with the content beneath it like
    any other in-flow element, right up until it is asked to pin.

    This step had zero scroll at all the day this test was written, which is
    why the old version read a single number at the top and called it a day.
    Task 2 gave the step two more required fields and, with them, its first
    real scroll - which is exactly the condition this test needs to say
    anything about `fixed` versus `sticky` at all, and exactly the condition
    the single-sample version could not survive.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    scrollable = page.evaluate("document.documentElement.scrollHeight - window.innerHeight")
    assert scrollable > 40, (
        f"this step does not scroll enough to tell sticky from fixed apart: {scrollable}px"
    )

    def gap_at(scroll_top):
        return page.evaluate(
            """(top) => {
              window.scrollTo(0, top);
              const el = document.querySelector('.step-nav');
              const rect = el.getBoundingClientRect();
              return {position: getComputedStyle(el).position,
                      gap: Math.round(window.innerHeight - rect.bottom)};
            }""",
            scroll_top,
        )

    samples = [gap_at(round(scrollable * fraction)) for fraction in (0, 0.4, 0.85)]
    assert all(sample["position"] == "sticky" for sample in samples), samples
    gaps = [sample["gap"] for sample in samples]
    #: **Non-decreasing, and strictly greater by the end** - not strictly
    #: growing at every sample, which is a claim about where the bar unpins
    #: rather than about `sticky` versus `fixed`. The three fractions were
    #: chosen when this step's only scroll came from Task 2's two fields; any
    #: copy added to step 3 lengthens the pinned region, and 0.4 then lands
    #: inside it and reads the same 0 as the top. Measured: `[0, 0, 159]` on
    #: this step once the production total explains itself. What still tells
    #: the two apart is the end - a `fixed` bar's gap never changes, so
    #: `gaps[0] < gaps[-1]` fails for it at any page height, which is asserted
    #: rather than inferred and is mutation-checked against
    #: `.step-nav { position: fixed }`.
    assert gaps[0] <= gaps[1] <= gaps[2], (
        f"the gap shrank while scrolling, which no sticky bar does: {gaps}"
    )
    assert gaps[0] < gaps[-1], (
        f"the gap never grew - a fixed bar reads this way too: {gaps}"
    )


def test_the_bar_unpins_above_the_footer_at_full_scroll(page_at):
    """Why no page-level padding is owed to the bar: at the end of the section
    it returns to its natural place, so nothing is permanently underneath it."""
    page = advance_to(page_at(1278, 983, 1.25), 3)
    measured = page.evaluate(
        """() => {
          window.scrollTo(0, document.documentElement.scrollHeight);
          const bar = document.querySelector('.step-nav').getBoundingClientRect();
          const footer = document.querySelector('footer').getBoundingClientRect();
          return {gap: Math.round(window.innerHeight - bar.bottom),
                  barBottom: Math.round(bar.bottom), footerTop: Math.round(footer.top)};
        }"""
    )
    assert measured["gap"] > 0, f"the bar is still pinned at full scroll: {measured}"
    assert measured["barBottom"] <= measured["footerTop"], measured


def test_the_allocation_summary_still_sticks(page_at):
    """The only other sticky in the flow. A new scroll container or any
    transformed ancestor kills it silently, and the destination step is the one
    screen with a sticky at both edges."""
    page = advance_to(page_at(1278, 983, 1.25), 3)
    measured = page.evaluate(
        """() => {
          const el = document.querySelector('.allocation-summary');
          window.scrollTo(0, 0);
          const natural = Math.round(el.getBoundingClientRect().top);
          const tops = [];
          for (const y of [natural + 200, natural + 500, 900]) {
            window.scrollTo(0, y);
            tops.push(Math.round(el.getBoundingClientRect().top));
          }
          return {position: getComputedStyle(el).position, natural, tops};
        }"""
    )
    assert measured["position"] == "sticky", measured
    assert all(abs(top - 10) <= 1 for top in measured["tops"]), measured


def test_the_allocation_summary_is_opaque_in_both_states(page_at):
    """...and being sticky, that is the only thing stopping rows showing through.

    The summary was `rgba(223, 248, 237, 0.68)`, and the destination rows scroll
    *underneath* it: labels and input borders were legible through the one panel
    on the screen whose whole job is to be readable while the list moves. The
    sibling test above proves it sticks; this one proves it covers.

    **Two assertions, because the fix had two halves.** Chromium serialises a
    fully opaque background as `rgb(...)` and anything less as `rgba(...)`, so
    the first is exactly "alpha is 1". The second pins the composite: the
    replacements are the old translucent colours resolved over `--kai-white`
    (#fff), which is the ground the summary sits on — 223/248/237 at 0.68 is
    233.24, 250.24, 242.76, and 255/80/50 at 0.07 is 255, 242.75, 240.65. So the
    panel is byte-identical to what it looked like before and only stops being
    see-through. An opaque background of some *other* colour would satisfy the
    first assertion and would be a redesign nobody asked for.

    Mutation: `KAICALC_MUTATION_CSS` carrying the two original `rgba(...)` rules
    fails both states.
    """
    page = advance_to(page_at(1278, 983, 1.25), 3)
    normal = page.evaluate(
        "() => getComputedStyle(document.querySelector('.allocation-summary')).backgroundColor"
    )
    page.fill('[data-line-field="amount"] >> nth=0', "1001")
    page.wait_for_timeout(80)
    invalid = page.evaluate(
        "() => getComputedStyle(document.querySelector('.allocation-summary.invalid')).backgroundColor"
    )
    measured = {"normal": normal, "invalid": invalid}
    assert not normal.startswith("rgba("), measured
    assert not invalid.startswith("rgba("), measured
    assert normal.replace(" ", "") == "rgb(233,250,243)", measured
    assert invalid.replace(" ", "") == "rgb(255,243,241)", measured


def test_every_focused_control_is_scrolled_clear_of_the_bar(page_at):
    """`scroll-margin-bottom`. Sequential focus navigation scrolls a control
    flush to the viewport edge, which is underneath a bar pinned there — a
    keyboard user typing into an input they cannot see.

    **Every** row, not the last one. Focusing the last row scrolls the document
    to its end, where the bar has already un-pinned, so that row clears the bar
    with the rule deleted and proves nothing: written that way this test
    survived its own mutation. Row 5 of 11 at 1278x983 is the case that
    discriminates — +16px of clearance with the rule, -69px without it.
    """
    page = advance_to(page_at(1278, 983, 1.25), 3)
    measured = page.evaluate(
        """() => {
          const inputs = [...document.querySelectorAll('[data-line-field="amount"]')];
          const rows = [];
          for (let i = 0; i < inputs.length; i++) {
            window.scrollTo(0, 0);
            inputs[i].focus();
            const rect = inputs[i].getBoundingClientRect();
            const bar = document.querySelector('.step-nav').getBoundingClientRect();
            rows.push({row: i, clear: Math.round(bar.top - rect.bottom), top: Math.round(rect.top)});
          }
          return rows;
        }"""
    )
    assert measured, "the destination step rendered no rows to focus"
    hidden = [row for row in measured if row["clear"] < 0 or row["top"] < 0]
    assert not hidden, f"focusing these rows put them behind the bar: {hidden}"


def test_the_bar_is_last_in_the_tab_order(page_at):
    """Tab must reach the form before the navigation, which is a DOM-order
    property of the bar being the last child of its section. This project
    already has one accessibility regression on record from a focus call in the
    wrong place, so it is asserted by actually pressing Tab."""
    page = advance_to(page_at(1278, 983, 1.25), 3)
    page.fill('[data-line-field="amount"] >> nth=0', "1000")
    page.wait_for_timeout(80)
    page.evaluate("window.scrollTo(0, 0); document.getElementById('main-content').focus({preventScroll: true})")
    order = []
    for _ in range(80):
        page.keyboard.press("Tab")
        stop = page.evaluate(
            """() => {
              const active = document.activeElement;
              if (!active || active === document.body || !active.closest('#main-content')) return null;
              const style = getComputedStyle(active);
              return {inBar: !!active.closest('.step-nav'),
                      label: (active.textContent || active.id || '').trim().slice(0, 24),
                      focusVisible: active.matches(':focus-visible'),
                      outline: style.outlineStyle,
                      bottom: Math.round(active.getBoundingClientRect().bottom),
                      viewport: window.innerHeight};
            }"""
        )
        if stop is None:
            break
        order.append(stop)
    in_bar = [index for index, stop in enumerate(order) if stop["inBar"]]
    assert in_bar, "Tab never reached the bar"
    assert in_bar == list(range(len(order) - len(in_bar), len(order))), (
        f"the bar is not last in the Tab order: {[stop['label'] for stop in order]}"
    )
    for stop in order:
        if stop["inBar"]:
            assert stop["focusVisible"] and stop["outline"] == "solid", stop
            assert stop["bottom"] <= stop["viewport"], stop


@pytest.mark.parametrize("width,height,dpr", VIEWPORTS + [pytest.param(960, 900, 1.0, id="960x900"),
                                                          pytest.param(1020, 900, 1.0, id="1020x900")])
def test_no_horizontal_overflow_at_any_breakpoint(page_at, width, height, dpr):
    """960 and 1020 are the `.wide` and `.results-page` bleed gates, and the
    arithmetic beside them in `styles.css` is what stops a section leaving the
    left edge. The bar spans those sections, so it is measured at both."""
    page = page_at(width, height, dpr)
    failures = []
    for step in walk(page):
        page.wait_for_timeout(100)
        measured = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
        )
        if measured["scroll"] > measured["client"]:
            failures.append(f"step {step} overflows by {measured['scroll'] - measured['client']}px")
    assert not failures, "; ".join(failures)


@pytest.mark.parametrize("width", [320, 390])
def test_the_longest_primary_label_does_not_overflow_the_narrowest_viewport(page_at, width):
    """The primary label is variable, and its longest form is the one the layout
    has to survive: "Calculate results for 2 entries" on the review step after a
    second supply-chain entry is added. The bar's middle track is
    `minmax(0, 1fr)` and its buttons do not carry `white-space: nowrap` for
    exactly this reason — an unwrappable `auto` grid track cannot shrink below
    its own text.

    **320 is the width that discriminates**, and it is a supported one:
    `body { min-width: 320px }`. Measured only at 390 both rules survive
    deletion, because "Calculate results for 2 entries" happens to fit there
    unwrapped; at 320 the same mutation puts 43px off the right edge and the
    bar's own `scrollWidth` 64px past its box.
    """
    page = advance_to(page_at(width, 700, 3.0), 4)
    page.click('[data-action="add-entry"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    press_continue(page)
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "500")
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    page.fill('[data-line-field="amount"] >> nth=0', "500")
    page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('[data-action="calculate"]')
    page.wait_for_timeout(120)
    measured = page.evaluate(
        """() => {
          const bar = document.querySelector('.step-nav');
          const primary = bar.querySelector('.button-primary');
          return {label: primary.textContent.trim(),
                  scroll: document.documentElement.scrollWidth,
                  client: document.documentElement.clientWidth,
                  barScroll: bar.scrollWidth, barClient: bar.clientWidth,
                  past: Math.round(primary.getBoundingClientRect().bottom - window.innerHeight)};
        }"""
    )
    assert "entries" in measured["label"], f"the long label was never rendered: {measured}"
    assert measured["scroll"] <= measured["client"], measured
    assert measured["barScroll"] <= measured["barClient"], f"the bar itself overflows: {measured}"
    assert measured["past"] <= 0, measured


def test_a_short_step_is_not_floored_by_a_stale_min_height(page_at):
    """`.main-content` once carried `min-height: calc(100vh - 220px)`,
    arithmetic over a header, a step-indicator band and a footer. That
    constant floored every step to the same height regardless of how much it
    actually rendered - so a step 87px shorter than the floor still produced
    a scrollbar with nothing below the fold to scroll to.

    **A document height that happens to equal the viewport, on the one step
    that happened to be this short, is not proof the floor is gone** - it is
    one coincidental sample, on one step, at one viewport, and it is exactly
    what broke the moment that step legitimately grew (Task 2's two money
    fields). Two things are asserted instead, neither of them that constant:

    1. the document shrinks at all when content is removed - a stale floor
       pinned to a fixed value would not move;
    2. once essentially everything the step rendered is gone, the document
       settles *exactly* at the viewport, not a few pixels above it. That is
       `body { min-height: 100vh }` - the one floor this project still
       promises, and the direction it guarantees is the opposite one: the
       document is never *shorter* than the viewport, never that it is
       floored *above* its own content. A few pixels of drift on an
       otherwise-empty step is this defect's own signature, at this
       viewport's own scale.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    viewport = page.evaluate("window.innerHeight")

    # First: the document tracks content at all. `.section-intro` is small
    # enough that what remains is still comfortably taller than the viewport,
    # so `body { min-height: 100vh }`'s own, legitimate floor cannot be the
    # thing making this pass.
    before = page.evaluate("document.documentElement.scrollHeight")
    removed = page.evaluate(
        """() => {
          const el = document.querySelector('.section-intro');
          const height = Math.round(el.getBoundingClientRect().height);
          el.remove();
          return height;
        }"""
    )
    after_one_block = page.evaluate("document.documentElement.scrollHeight")
    assert after_one_block < before, (
        f"removing a {removed}px block left the document unchanged - {before} -> {after_one_block} - a stale floor"
    )

    # Second, and this is the part that actually distinguishes the two
    # floors: strip essentially everything the step rendered and see where
    # the document settles. Without a stale floor it settles exactly at the
    # viewport - the legitimate one. With the historical
    # `calc(100vh - 220px)` constant back on `.main-content` it settles a few
    # pixels above it instead, because that arithmetic no longer matches the
    # header/footer chrome it was written against once the step-indicator
    # band moved into the bar - the same drift that produced the original
    # 87px defect, just measured at this viewport's own scale.
    stripped = page.evaluate(
        """() => {
          document.querySelector('.amount-grid')?.remove();
          return document.documentElement.scrollHeight;
        }"""
    )
    assert stripped <= viewport + 5, (
        f"stripping the step's own content still leaves a {stripped}px document in a {viewport}px viewport - "
        "a stale floor is holding it up above its own content"
    )


def test_the_step_position_moved_into_the_bar_and_left_no_band_behind(page_at):
    """The 87px. The bar has to *say* which step this is, or the band was
    deleted rather than folded in — and a second progress element anywhere on
    the page spends the space again."""
    page = advance_to(page_at(1278, 983, 1.25), 2)
    measured = page.evaluate(
        """() => ({
          label: document.querySelector('.step-nav .step-nav-label')?.textContent?.trim() || null,
          name: document.querySelector('.step-nav .step-nav-name')?.textContent?.trim() || null,
          bands: document.querySelectorAll('#step-indicator, .step-indicator, .step-mobile').length,
          mainTop: Math.round(document.getElementById('main-content').getBoundingClientRect().top),
        })"""
    )
    assert measured["label"] == "Step 3 of 6", measured
    assert measured["name"] == "Waste amount", measured
    assert measured["bands"] == 0, f"a progress band is still costing height at the top: {measured}"


def _improvement_panel(page_at):
    """A page at step 5 (results) with the improvement panel open.

    Shared by every test in this module that needs the destination-allocation
    sliders — factored out rather than repeated so the wizard walk that reaches
    them is written once.
    """
    page = advance_to(page_at(1278, 983, 1.25), 5)
    page.click('[data-action="explore-improvements"]')
    return page


def test_the_improvement_percentage_refuses_a_minus_without_rewriting_the_number(page_at):
    """A negative share of a destination is not a thing, so the minus is refused
    outright — unlike a destination amount, which permits a leading minus
    precisely so that `validateCurrentStep`'s refusal has something to point at.

    The second half is the affirmative one and it is the reason this test is not
    just `assert "-" not in value`. A clamp would also produce a field with no
    minus in it, while destroying the number the visitor typed — that is exactly
    what `updateImprovementInput`'s `Math.min(100, Math.max(0, …))` did before
    PR #27 removed it, turning a typed `0.05` into `5`. The guard must block the
    minus and touch nothing else.
    """
    page = _improvement_panel(page_at)
    number = page.locator('.percentage-input input[type="number"]').first

    number.press("ControlOrMeta+A")
    number.press("Backspace")
    number.press_sequentially("--1")
    assert number.input_value() == "1"

    number.press("ControlOrMeta+A")
    number.press("Backspace")
    number.press_sequentially("205")
    assert number.input_value() == "205", (
        "the guard rewrote the visitor's number; it may only refuse the minus"
    )

    # Driving the slider needs a value away from its ceiling, and the reason is
    # itself worth stating: typing `205` above mirrored straight into this
    # sibling `input[type=range]`, whose `max="100"` made the browser clamp it —
    # which is how we know `updateImprovementInput`'s mirroring is live. From
    # 100 an ArrowUp has nowhere to go, so the field is reset first.
    number.press("ControlOrMeta+A")
    number.press("Backspace")
    number.press_sequentially("10")

    slider = page.locator('input[type="range"][data-improvement-code]').first
    before = slider.input_value()
    slider.press("ArrowUp")
    assert slider.input_value() != before, "the slider stopped responding"
    assert number.input_value() == slider.input_value()

    # The same invariant in the direction that actually breaks it, and the reason
    # the range carries `step="any"` rather than any fixed figure.
    # `<input type="range">` snaps *anything* assigned to `.value` to a multiple of
    # its own step; `<input type="number">` does not. So a range stepped more
    # coarsely than the box beside it leaves one allocation showing as two numbers
    # — a box reading 39.55 next to a slider sitting on 40 — and the visitor has no
    # way to tell which of the two the Compare button is about to send.
    #
    # Stage four is what made the two agree, and it did it by removing the range's
    # native step rather than by matching it to the box's: `updateImprovementInput`
    # rounds a *drag* to `rangeStep`'s own coarseness itself, so a drag still lands
    # on a nameable number while an exact figure mirrored in from the box arrives
    # unrounded. 39.55 is representable at the box's step and at no coarser one, so
    # this fails the moment a native step comes back onto the range — checked by
    # putting `step="5"` back and watching it report `box 39.55, slider 40`.
    number.press("ControlOrMeta+A")
    number.press("Backspace")
    number.press_sequentially("39.55")
    assert number.input_value() == "39.55"
    assert slider.input_value() == number.input_value(), (
        "the slider and the number box are showing different numbers for one "
        f"allocation: box {number.input_value()}, slider {slider.input_value()}"
    )


def test_the_kilogram_guard_refuses_an_allocation_the_percentage_guard_allows(page_at):
    """`improvementValidation` guards the allocation twice, and the second guard
    is not a belt-and-braces afterthought — it is the one that fires at ordinary
    scales.

    60 + 40.005 totals 100.005%, and |100.005 - 100| is 0.005, *inside* the
    0.01-percentage-point tolerance. The percentage guard passes it. On this
    journey's 1,000 kg the same allocation describes 1,000.05 kg of waste, which
    is 0.05 kg heavier than the current scenario and five times
    `MASS_TOLERANCE_KG`, so Compare Impact is refused by the kilogram guard
    alone.

    That refusal is the domain rule, not a preference: both scenarios have to
    move the same mass, or a net benefit can be inflated by quietly assuming
    less waste in the alternative. A percentage tolerance cannot express it,
    because 0.01 percentage points is a different number of kilograms at every
    tonnage.
    """
    page = advance_to(page_at(1278, 983, 1.25), 5)
    page.click('[data-action="explore-improvements"]')
    boxes = page.locator('.percentage-input input[type="number"]')
    assert boxes.count() >= 2, "this needs two destinations to split an allocation across"

    # The panel opens seeded from the current scenario, so every row is cleared
    # before the two under test are set; otherwise the seeded 100% is still in
    # the total and the percentage guard would refuse it too, for the wrong
    # reason.
    for index in range(boxes.count()):
        boxes.nth(index).fill("0")
    boxes.nth(0).fill("60")
    boxes.nth(1).fill("40.005")

    total = sum(float(boxes.nth(i).input_value() or 0) for i in range(boxes.count()))
    assert abs(total - 100) <= 0.01, (
        f"the premise of this test has moved: {total} is no longer inside the "
        "percentage tolerance, so it no longer isolates the kilogram guard"
    )
    compare = page.locator('[data-action="compare-improvement"]')
    assert compare.is_disabled(), (
        "Compare Impact was offered on an allocation 0.05 kg heavier than the "
        "current scenario; the kilogram guard is not firing"
    )
    assert page.locator("#improvement-inline-error").is_visible()

    # The positive control, on the same two rows: the refusal is about the
    # 0.005, not about the panel refusing everything.
    boxes.nth(1).fill("40")
    assert compare.is_enabled(), "an exact 60/40 split was refused"


def test_the_improvement_donut_draws_the_share_the_slider_holds(page_at):
    """The donut is display only (§7.6.1): every slice is an angle turned from a
    percentage the panel already shows as a number, never a second derivation of
    it. So the callout beside a slice has to read back the figure typed into the
    box, and it has to keep doing so on the keystroke path — which patches the
    DOM instead of re-rendering, precisely so the caret survives mid-number, and
    would therefore leave a stale chart if `updateImprovementInput` did not redraw
    it by hand.
    """
    page = advance_to(page_at(1278, 983, 1.25), 5)
    page.click('[data-action="explore-improvements"]')
    assert page.locator(".improvement-pie-chart").count() == 1

    number = page.locator('.percentage-input input[type="number"]').first
    number.press("ControlOrMeta+A")
    number.press("Backspace")
    number.press_sequentially("42.5")
    #: `all_inner_texts` is `innerText`, which an SVG element does not have; the
    #: callouts came back as `[None, None]` before this read `textContent`.
    labels = page.locator(".improvement-pie-label text").all_text_contents()
    assert "42.5%" in labels, (
        f"the donut did not redraw to the typed allocation; callouts were {labels}"
    )

    # Enlarging it is a second copy of the same chart, not a second chart: the
    # dialog reads the same state, so it cannot show a different allocation.
    page.click('[data-action="expand-improvement-chart"]')
    assert page.locator(".improvement-chart-modal .improvement-pie-chart").count() == 1
    page.click('[data-action="close-improvement-chart"]')
    assert page.locator(".improvement-chart-modal").count() == 0


def test_the_improvement_chart_follows_both_scroll_directions_on_desktop(page_at):
    """The chart card stays beside the long allocation list while it is being edited.

    This measures the rendered card rather than merely checking ``position: sticky``:
    a sticky element whose containing block is too short, whose ancestor clips it, or
    whose inset is missing computes as sticky but still scrolls away.  Moving down and
    then back up also covers the client's explicit requirement that the visual follow
    the viewport in both directions.
    """
    page = advance_to(page_at(1278, 700, 1), 5)
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector(".improvement-pie-wrap")

    positions = page.evaluate(
        """async () => {
          const editor = document.querySelector('.improvement-editor');
          const card = editor.querySelector('.improvement-pie-wrap');
          const editorTop = editor.getBoundingClientRect().top + window.scrollY;
          window.scrollTo(0, editorTop + 20);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const down = card.getBoundingClientRect().top;
          window.scrollBy(0, 20);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const fartherDown = card.getBoundingClientRect().top;
          window.scrollBy(0, -10);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const backUp = card.getBoundingClientRect().top;
          return {
            position: getComputedStyle(card).position,
            inset: parseFloat(getComputedStyle(card).insetBlockStart),
            down,
            fartherDown,
            backUp,
            editorBottom: editor.getBoundingClientRect().bottom,
            cardBottom: card.getBoundingClientRect().bottom,
          };
        }"""
    )

    assert positions["position"] == "sticky", positions
    for name in ("down", "fartherDown", "backUp"):
        assert abs(positions[name] - positions["inset"]) <= 1, positions
    assert positions["cardBottom"] <= positions["editorBottom"] + 1, (
        f"the sticky chart escaped the editor that owns it: {positions}"
    )


def test_the_improvement_copy_uses_the_visuals_left_edges(page_at):
    """The intro aligns with the section title and the error with the total label.

    Both lines used to inherit the same centred 720px text column, even though the
    elements they describe start at two different edges.  Measure the rendered text
    boxes so a future shorthand margin cannot silently centre them again.
    """
    page = advance_to(page_at(1278, 800, 1), 5)
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector("#improvement-inline-error")

    edges = page.evaluate(
        """() => {
          const metrics = selector => {
            const element = document.querySelector(selector);
            const box = element.getBoundingClientRect();
            return {
              left: box.left,
              lines: Math.round(box.height / parseFloat(getComputedStyle(element).lineHeight)),
              fontSize: parseFloat(getComputedStyle(element).fontSize),
            };
          };
          return {
            title: metrics('#improvement-title'),
            intro: metrics('.improvement-scenario > p:first-of-type'),
            totalLabel: metrics('.improvement-total > span:first-child'),
            error: metrics('#improvement-inline-error'),
          };
        }"""
    )

    assert abs(edges["intro"]["left"] - edges["title"]["left"]) <= 1, edges
    assert abs(edges["error"]["left"] - edges["totalLabel"]["left"]) <= 1, edges
    assert edges["intro"]["lines"] == 1, edges
    assert edges["error"]["fontSize"] == edges["intro"]["fontSize"], edges


#: Room Chromium reserves for the native dropdown arrow, **inside** the select's
#: own padding box. A control exactly as wide as its longest option plus its
#: horizontal padding is therefore still too narrow by this much, which is why
#: `MODE_SELECT_FIT` below adds it rather than comparing against the text alone.
#:
#: **Measured, not assumed.** `clientWidth` of a `width: max-content` clone of
#: `#improvement-mode`, minus (longest option measured in the select's own
#: computed font + `paddingLeft` + `paddingRight`), came to exactly 20.0px in
#: English, German and Arabic, at every width in `_FIVE_WIDTHS` plus 768 and 803.
#: The 1px of slack each assertion allows on top is for `clientWidth` being an
#: integer where the text measurement is fractional, not for the arrow.
_SELECT_ARROW_PX = 20

#: How much width `#improvement-mode` needs to draw its own longest option, how
#: much it has, and how much the panel could possibly give it.
#:
#: **`scrollWidth` cannot answer this question.** A closed `<select>` paints an
#: ellipsis or simply cuts its label at the content edge; it does not lay the
#: text out past that edge and does not become scrollable, so `clientWidth ==
#: scrollWidth` holds whether the label fits or not - measured 98/98, 107/107,
#: 122/122 and so on across the whole band where it was in fact clipped. Every
#: overflow assertion in this file is therefore blind to it, which is why this
#: measures the *text* against the box: the widest option is drawn into a canvas
#: with the select's own computed font, and `paddingLeft` + `paddingRight` +
#: `_SELECT_ARROW_PX` added to it.
#:
#: The options are read off the live element rather than named here, so this
#: measures whichever of the twenty catalogues is in force - and, since #74,
#: whichever containers the published taxonomy carries.
#:
#: **`room` is new with #74 and it is what keeps the assertion honest at 320px.**
#: The control's options now include every `unit_preset` label, which is staff
#: text printed verbatim (§7.7.7) and so the same width in all twenty
#: catalogues: "23 L kerbside food scraps bin (full)" needs 300px, and the panel
#: leaves 242px at a 320px viewport. `max-width: 100%` is what stops that
#: becoming 58px of page overflow, and the result is a label clipped because
#: there is no room - which is a different fact from a label clipped because the
#: control took its width from a layout column, and the helper below has to be
#: able to tell them apart. `room` is the widest `clientWidth` this control could
#: have inside its own flex container, borders discounted the same way
#: `clientWidth` discounts them.
MODE_SELECT_FIT = """
async () => {
  await document.fonts.ready;
  const select = document.querySelector('#improvement-mode');
  const style = getComputedStyle(select);
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  let longest = '', textWidth = 0;
  for (const option of select.options) {
    const width = ctx.measureText(option.textContent).width;
    if (width > textWidth) { textWidth = width; longest = option.textContent; }
  }
  const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
  const border = parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth);
  const field = select.closest('.improvement-mode-field');
  return {
    longest,
    textWidth,
    padding,
    text: textWidth + padding,
    clientWidth: select.clientWidth,
    scrollWidth: select.scrollWidth,
    room: field.clientWidth - border,
    options: select.options.length,
    title: select.getAttribute('title'),
    selectedLabel: select.options[select.selectedIndex].textContent,
  };
}
"""


def assert_the_mode_select_is_sized_to_its_own_longest_option(page, where):
    """`#improvement-mode` is as wide as the widest option it offers, or as wide as
    the panel leaves it - whichever is smaller - and no wider.

    Two assertions, because the two directions are two different mistakes and one
    message should not have to describe both. Too narrow is the measured defect: the
    control took its width from a layout column and cut its own label. Too wide is
    what "sized to its content" rules out - a control stretched across the whole
    panel by the site-wide ``input, select { width: 100% }`` - and is the only thing
    standing in for the end-edge pin this control deliberately no longer has.

    **The ``min`` against ``room`` is #74's own arithmetic and it is not a
    relaxation.** The control's longest option is now a staff-typed container label
    - 300px with padding and arrow, identical in all twenty catalogues because staff
    text is never translated - and a 320px viewport leaves the panel 242px. There is
    no width to take from anywhere: the only choices at that viewport are to clip or
    to push the control out of the page, and `max-width: 100%` chooses the first
    (the full name stays reachable through the native dropdown, which draws options
    at their own width, and through the select's own `title`, asserted separately by
    `test_the_one_unit_control_names_its_own_selection_in_full`).

    What the ``min`` does **not** admit is the v1.82 defect: a control that is
    narrower than both its longest option and the room it was given still fails
    here, at every width and in every script, which is what the mutation
    ``.improvement-mode-field select { width: 79px !important }`` is for.
    """
    fit = page.evaluate(MODE_SELECT_FIT)
    needs = fit["text"] + _SELECT_ARROW_PX
    target = min(needs, fit["room"])
    capped = "" if needs <= fit["room"] else (
        f" (capped at the {fit['room']}px the panel leaves it, 'max-width: 100%'; "
        f"the option itself needs {needs:.1f}px and is clipped by "
        f"{needs - fit['room']:.1f}px there with the full name on the select's own title)"
    )
    assert fit["clientWidth"] + 1 >= target, (
        f"[{where}] the unit-mode select is {fit['clientWidth']}px wide inside its padding "
        f"but needs {target:.1f}px to draw {fit['longest']!r}{capped} "
        f"({fit['textWidth']:.1f}px of text + {fit['padding']:.1f}px padding + "
        f"{_SELECT_ARROW_PX}px for the dropdown arrow) - the option is being clipped, "
        f"silently: scrollWidth is {fit['scrollWidth']}, the same as clientWidth, because "
        "a closed select cuts its label rather than overflowing"
    )
    #: 2px, for an integer `clientWidth` measured against a fractional canvas
    #: measurement. The two agreed to 0.0px in English, German, Russian and Arabic
    #: at 320/390/700/768/803/1278, so this is rounding slack and nothing else -
    #: it is three hundred-odd pixels short of admitting a full-width control.
    assert fit["clientWidth"] <= target + 2, (
        f"[{where}] the unit-mode select is {fit['clientWidth']}px wide inside its padding "
        f"where {target:.1f}px draws its longest option {fit['longest']!r}{capped} - it is "
        "taking its width from its container rather than from its own content"
    )
    return fit


def test_the_unit_mode_control_sits_in_one_row_above_the_chart(page_at):
    """The global mode selector belongs to the visual it changes.

    It starts at the chart's own inline-start edge rather than at a separately centred
    width, and its label and select share one row.  Measuring the rendered boxes
    protects both parts of the request: horizontal label/control layout and placement
    directly above the chart.

    **The end edge is deliberately not asserted, and that is a correction.** The first
    version of this test pinned `field.right` to `chart.right` as well, which made the
    control's width the chart column's width - `minmax(144px, 0.72fr)`, so 144px at the
    700px seam - and left it too narrow for its own longest option from 700px to about
    780px in English and across the whole 700-803px band in German.  What the request
    asked for is the alignment with the visual below, and that is the start edge; the
    property the end edge was standing in for is "wide enough for what it says", which
    is asserted here directly and at the widths where it bites by
    `test_the_unit_mode_select_can_draw_its_own_longest_option`.
    """
    page = advance_to(page_at(1278, 800, 1), 5)
    page.click('[data-action="explore-improvements"]')

    layout = page.evaluate(
        """() => {
          const box = selector => {
            const rect = document.querySelector(selector).getBoundingClientRect();
            return {
              left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
              middle: rect.top + rect.height / 2,
            };
          };
          return {
            intro: box('.improvement-scenario > p:first-of-type'),
            field: box('.improvement-mode-field'),
            label: box('.improvement-mode-field label'),
            select: box('#improvement-mode'),
            chart: box('.improvement-pie-wrap'),
          };
        }"""
    )

    assert layout["field"]["left"] == pytest.approx(layout["chart"]["left"], abs=1)
    assert layout["field"]["top"] >= layout["intro"]["bottom"], layout
    assert layout["field"]["bottom"] <= layout["chart"]["top"], layout
    assert layout["label"]["right"] < layout["select"]["left"], layout
    assert layout["label"]["middle"] == pytest.approx(layout["select"]["middle"], abs=1)
    assert_the_mode_select_is_sized_to_its_own_longest_option(page, "en@1278")


@pytest.mark.parametrize("lang", ["en", "de", "ru", "ar"])
@pytest.mark.parametrize("width", [320, 700, 768])
def test_the_unit_mode_select_can_draw_its_own_longest_option(page_at_locale, width, lang):
    """The unit/percentage control fits its own label at every width and in every script.

    **700-768px is the band where taking the control's width from the chart column cost
    it its own text.** Measured on the first version of this layout, with no allowance
    at all for the dropdown arrow: at 700px the select was 100px wide against 111px of
    `Percentage` plus padding, and 79px against 114px of `Prozentsatz`; at 768px -
    which `styles.css` names as the likeliest non-desktop demo device - 108.6 against
    111, and 87.6 against 114.  Russian was worse again, 79px against 170px, and Arabic
    was the only one of the four that was clear.

    **No other assertion in this file could see it.** `clientWidth == scrollWidth` held
    throughout, so the overflow checks had nothing to report, and the layout checks were
    satisfied by a control pinned to exactly the width that was too small - see
    `MODE_SELECT_FIT`.  Deleting the declaration that caused the clipping
    (`.improvement-mode-field select { min-width: 0 }`) left all seven of that round's
    new cases green, which is what says the suite was not watching.

    **320px is the other half, and it is a different mechanism.** There the panel leaves
    242px, the label "Единица измерения" takes 144px of it on its own, and one row
    cannot hold both - so the row wraps and the control keeps its width on a line of its
    own.  Wrapping rather than a breakpoint is deliberate: what has to fit is a
    translated string, and no viewport width predicts which catalogue is in force.

    **#74 made 320px the one width where the control cannot hold its own longest option
    at all, and that is measured rather than conceded.** Collapsing the per-row unit
    selectors into this one control puts every `unit_preset` container among its options,
    and a container label is staff text printed verbatim (§7.7.7), so the widest option
    is the same in every catalogue: "23 L kerbside food scraps bin (full)", 254px of text
    and 300px with padding and arrow, against the 242px the panel leaves at 320px. It
    fits from 390px (312px) up and is clipped by 60px at 320px, where `max-width: 100%`
    is the only alternative to 58px of page overflow. `MODE_SELECT_FIT`'s `room` is what
    lets the helper say which of those two happened; the full name is still reachable,
    from the native dropdown (which draws options at their own width, not the closed
    control's) and from the select's `title`.

    **What `flex-wrap: wrap` guards there is the overflow, not the clipping, and that
    was measured rather than assumed.** A flex item's automatic minimum size stops this
    `<select>` shrinking below its own longest option at all, so making the line
    `nowrap` leaves the control full width and pushes it out of the panel instead:
    `documentElement.scrollWidth` goes 9px past the viewport at 320px in Russian, while
    every width assertion above still passes.  Hence the overflow check below, which is
    the assertion that kills that mutation.  9px is a floor, not the figure a visitor
    sees - the shared `browser` fixture launches Chromium with Playwright's default
    `--hide-scrollbars`, so it reports the viewport about 15px wider than a real one
    (see `test_results_floating_nav_browser.py`, which overrides that for exactly this
    reason).

    German rather than English alone because German is where the measured shortfall in
    the band was largest; Russian because it held the widest *translated* label on this
    control (190px against German's 134px), which made it the worst case in the band and
    is still the only one that wraps the flex line at 320px; and Arabic because the
    start edge it aligns to is the opposite one, and a start-edge assertion that has
    only ever run LTR is half a test.
    """
    page = advance_to(page_at_locale(width, 900, 1.0, lang), 5)
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector("#improvement-mode")

    assert_the_mode_select_is_sized_to_its_own_longest_option(page, f"{lang}@{width}")

    #: The alignment half of the same request, at the widths this test already
    #: has open: the control's inline-start edge is the chart card's, which is
    #: `left` in English, German and Russian and `right` in Arabic.
    edges = page.evaluate(
        """() => {
          const rtl = getComputedStyle(document.documentElement).direction === 'rtl';
          const start = selector => {
            const rect = document.querySelector(selector).getBoundingClientRect();
            return rtl ? rect.right : rect.left;
          };
          return {
            field: start('.improvement-mode-field'),
            chart: start('.improvement-pie-wrap'),
            scroll: document.documentElement.scrollWidth,
            client: document.documentElement.clientWidth,
          };
        }"""
    )
    assert edges["field"] == pytest.approx(edges["chart"], abs=1), (
        f"[{lang}@{width}] the mode control's start edge ({edges['field']}) left the chart "
        f"card's ({edges['chart']})"
    )
    assert edges["scroll"] <= edges["client"], (
        f"[{lang}@{width}] the page is {edges['scroll'] - edges['client']}px wider than its "
        "own viewport with the improvement panel open - the mode control keeps its width "
        "and takes it out of the panel rather than off its own label"
    )


@pytest.mark.parametrize("width", [699, 560, 559, 390])
def test_the_improvement_visual_and_controls_reflow_without_horizontal_overflow(page_at, width):
    """Resize boundaries keep the chart, caption/control and editor in one viewport.

    699px is one pixel below the two-column budget; 560/559 straddle the allocation
    row's own minimum; and 390px is the client's phone viewport.  These are the seams
    where a broad phone-only rule would otherwise leave an untested overflow band.
    """
    page = advance_to(page_at(1278, 800, 1), 5)
    page.click('[data-action="explore-improvements"]')
    page.set_viewport_size({"width": width, "height": 800})
    page.wait_for_timeout(100)

    layout = page.evaluate(
        """() => {
          const root = document.documentElement;
          const editor = document.querySelector('.improvement-editor');
          const card = editor.querySelector('.improvement-pie-wrap');
          const mode = document.querySelector('.improvement-mode-field');
          const chart = card.querySelector('.improvement-pie-chart');
          const button = card.querySelector('.improvement-expand-chart');
          const list = editor.querySelector('.improvement-allocation-list');
          const row = list.querySelector('.improvement-allocation-row');
          const box = element => {
            const rect = element.getBoundingClientRect();
            return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom};
          };
          return {
            viewport: window.innerWidth,
            scrollWidth: root.scrollWidth,
            columns: getComputedStyle(editor).gridTemplateColumns.split(' ').length,
            cardPosition: getComputedStyle(card).position,
            mode: box(mode),
            card: box(card),
            chart: box(chart),
            button: box(button),
            list: box(list),
            rowColumns: getComputedStyle(row).gridTemplateColumns.split(' ').length,
          };
        }"""
    )

    assert layout["scrollWidth"] <= layout["viewport"], layout
    assert layout["columns"] == 1, layout
    assert layout["cardPosition"] == "static", layout
    assert layout["list"]["top"] >= layout["card"]["bottom"] - 1, layout
    for visual in ("mode", "card", "chart", "button", "list"):
        assert layout[visual]["left"] >= -1, (visual, layout)
        assert layout[visual]["right"] <= layout["viewport"] + 1, (visual, layout)
    if width <= 559:
        assert layout["rowColumns"] == 1, layout


#: The largest fraction this stylesheet's `max-width` convention leaves unclaimed
#: below the `min-width` it complements. `@media (max-width: 1019.98px)` against
#: `@media (min-width: 1020px)` is the pair that established it in this file, and
#: the improvement panel's `699.98` / `700` follows it. It is a convention rather
#: than a closure: 0.02px stays unclaimed, which is 1/50 of what an integer
#: `max-width` leaves and below the granularity any device scaling produces.
_BREAKPOINT_TOLERANCE = 0.02


def _improvement_media_blocks():
    """`(min_widths, max_widths, declarations)` per `@media` block that governs the
    improvement panel.

    Reads `web/css/styles.css` from the working tree, not the served stylesheet:
    these are assertions about what the file says, so they need neither the stack
    on :18080 nor the `web` image rebuilt.

    Comments are stripped first, and that is belt-and-braces rather than a fix for
    anything the file does today: measured both ways, the bounds that come back are
    identical, because every breakpoint number this stylesheet quotes in prose sits
    *before* its `@media` token rather than between it and the `{`. It is kept
    because both of those are legal CSS - a comment inside a prelude would be read as
    part of the query, and a comment naming the panel inside an unrelated block would
    pull that block into this list.
    """
    source = (ROOT / "web" / "css" / "styles.css").read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    blocks = []
    for match in re.finditer(r"@media([^{]*)\{", body):
        prelude = match.group(1)
        depth, index = 1, match.end()
        while depth and index < len(body):
            depth += {"{": 1, "}": -1}.get(body[index], 0)
            index += 1
        declarations = body[match.end():index - 1]
        if "improvement" not in declarations:
            continue
        blocks.append((
            [float(value) for value in re.findall(r"min-width:\s*([\d.]+)px", prelude)],
            [float(value) for value in re.findall(r"max-width:\s*([\d.]+)px", prelude)],
            declarations,
        ))
    return blocks


def test_the_improvement_panels_breakpoints_tile_without_a_gap():
    """A `max-width` and the `min-width` it complements leave no width between them.

    **This reads the two rules instead of measuring at a width, because the defect is
    a relationship and not a pixel.** `@media (max-width: 699px)` against `@media
    (min-width: 700px)` leaves every used viewport width in the open interval `(699,
    700)` matched by neither, and those widths exist. Measured on a 699px window at
    forced device scale factors of 1.1, 1.25 and 1.5, the used width was 699.107,
    699.216 and 699.349, `matchMedia` answered **false** to `(max-width: 699px)` and
    **false** to `(min-width: 700px)` at all three, and `.improvement-editor` took its
    base two-column template there (`143.991px 440px`) while `.improvement-pie-wrap`
    stayed `static` - the desktop grid with the sticky half missing, which is a
    pairing no width is meant to produce.

    **Nothing in this file could have caught that in a browser, for two separate
    reasons.** Playwright's `viewport` is `Emulation.setDeviceMetricsOverride`, whose
    `width` is an integer and which rejects a fractional one outright ("Invalid
    parameters"), so the shared `browser` fixture cannot reach the band at all;
    reaching it needs a Chromium launched with `--force-device-scale-factor=s` and a
    context with `no_viewport=True`, where the fraction comes from the physical-pixel
    rounding `round(W * s) / s`. And the symptom is not an overflow: the 440px
    allocation list overhangs the editor's grid area by 0.67-0.88px and the panel's
    own 26px padding absorbs every bit of it, so `documentElement.scrollWidth -
    clientWidth` measured 0 in the band and 0 at both 699 and 700, with and without
    `--hide-scrollbars`. The reflow test just above is this panel's overflow check,
    and it reads 0 against this defect at every width it runs.

    **What it assumes, stated rather than hidden:** that every `min-width` governing
    this panel is the complement of a `max-width`, so the nearest `max-width` below it
    is the rule it hands over from. That is true of the panel today - one `min-width`
    (the sticky card) against three `max-width` rules - and it is the convention this
    file holds itself to. A future `min-width` enhancement with no else-branch would
    fail here, and the right answer then is to say so in this test, not to widen the
    tolerance.

    Scoped to the `@media` blocks whose declarations name the improvement panel. This
    stylesheet has two other `max-width` / `min-width` pairs an off-by-one apart -
    `649` / `650` and `999` / `1000`, on the public header and the home page - which
    are older than this panel and are not this change's subject.
    """
    blocks = _improvement_media_blocks()
    lower_bounds = sorted({value for mins, _, _ in blocks for value in mins})
    upper_bounds = sorted({value for _, maxes, _ in blocks for value in maxes})
    assert lower_bounds and upper_bounds, (
        "no width-bounded `@media` rule governs the improvement panel any more - this "
        f"test has stopped reading the file it thinks it is reading (blocks: {len(blocks)})"
    )

    gaps = []
    for lower in lower_bounds:
        below = [value for value in upper_bounds if value < lower]
        if not below:
            continue
        nearest = max(below)
        if lower - nearest > _BREAKPOINT_TOLERANCE:
            gaps.append((nearest, lower))
    assert not gaps, (
        "the improvement panel's breakpoints do not tile: "
        + "; ".join(
            f"every used viewport width in ({nearest:g}, {lower:g}) is matched by neither "
            f"`max-width: {nearest:g}px` nor `min-width: {lower:g}px`, and fractional device "
            f"scaling produces such widths - write the max-width as "
            f"{lower - _BREAKPOINT_TOLERANCE:g}px, as the 1019.98/1020 pair in this file does"
            for nearest, lower in gaps
        )
    )


def test_the_allocation_rows_stacking_breakpoint_is_the_width_these_tests_name():
    """`_STACKING_BREAKPOINT` is the stylesheet's own boundary, not a comment about it.

    Two tests in this file branch on 560: the reflow test above, through its `[699,
    560, 559, 390]` parametrisation, where 560 is deliberately the *unstacked* side of
    the boundary and 559 the stacked one, and
    `test_the_range_gets_more_room_than_the_select_in_unit_mode` below, through `width
    >= _STACKING_BREAKPOINT`. Neither of them reads the stylesheet, so both would go
    on passing against a breakpoint that had moved: the reflow test's `if width <=
    559` guard only ever asserts the stacked behaviour, so a stylesheet that stacked
    at 560 as well would satisfy it while the 560 case silently stopped being the
    control it was chosen to be.

    So the constant is pinned to the rule. 560 is also the narrow-layout breakpoint
    `.public-header-inner` and the home page already use, which is why the panel's own
    rule is written as `559.98` - the same boundary, in the `.98` form that keeps 560
    itself on the unstacked side - rather than as the `559` it first carried, a number
    with no arithmetic behind it.
    """
    stacking = [
        (maxes, declarations)
        for _, maxes, declarations in _improvement_media_blocks()
        if ".improvement-allocation-row" in declarations
    ]
    assert len(stacking) == 1, (
        f"expected exactly one `@media` block to stack `.improvement-allocation-row`, "
        f"found {len(stacking)}"
    )
    maxes, _ = stacking[0]
    #: `pytest.approx` because `560 - 0.02` is not the same double as `float("559.98")`
    #: for every pair of values this arithmetic could be given, and the assertion is
    #: about the boundary rather than about IEEE 754.
    assert maxes == pytest.approx([_STACKING_BREAKPOINT - _BREAKPOINT_TOLERANCE]), (
        f"the allocation row stacks at `max-width: {'/'.join(f'{m:g}' for m in maxes)}px` "
        f"but every width-dependent "
        f"assertion in this file is written against _STACKING_BREAKPOINT = "
        f"{_STACKING_BREAKPOINT}, so the boundary has to be "
        f"{_STACKING_BREAKPOINT - _BREAKPOINT_TOLERANCE:g}px - 560 itself stays unstacked"
    )


def test_step_three_asks_what_the_stage_put_through(page_at):
    """Item ④. Without it the results page can never state waste as a share
    of production, which is the figure the client asked for - and the reason
    the old percentage was removed rather than fixed: `results.js` carries a
    note saying it was "a number the engine never produced".

    Optional, and the label says so. A visitor who does not know their
    production total still gets every other figure, so this must not become a
    fourth required field on a step that already has two.
    """
    #: `walk()`'s numeric yields are the screen sequence (0 sector, 1 food, 2
    #: amount, 3 destination, ...), one behind the UI's own 1-based "Step 3"
    #: label on the amount screen this field lives on - `2` is the amount
    #: screen; `3` is the destination-allocation screen one step later, whose
    #: `#total-waste` this file's other tests fill, never re-fill.
    page = advance_to(page_at(1278, 983, 1.25), 2)

    field = page.locator("#total-input")
    assert field.count() == 1, "step 3 has no production-total field"
    label = page.locator('label[for="total-input"]').inner_text()
    assert "optional" in label.lower(), (
        f"the field does not say it is optional: {label!r}"
    )

    #: Above the fold on the smallest viewport this project supports. Step 3
    #: already carries an amount, a unit and a container hint; a fourth
    #: control that pushes Continue off the screen is the defect
    #: `test_the_primary_action_of_every_step_is_reachable_without_scrolling`
    #: exists for.
    box = field.bounding_box()
    assert box["y"] < page.evaluate("window.innerHeight")


def test_step_three_forms_two_aligned_groups_above_the_breakpoint(page_at):
    """Two vertical groups, not a three-column row and not five equal cells.

    The two are ``.zone`` sections now — "what this unit measures" (the unit,
    the waste amount, the production total) and "supporting figures" (the two
    optional NZ$ ones) — each a single item in ``.zones``'s two-column row at
    ``min-width: 650px``. They were ``.mass-fields`` and ``.money-fields``, bare
    wrappers with no tint and no heading, until the zoning change of
    2026-09-19; the grouping this test measures is the same grouping, and the
    assertions are untouched. Every field in the first group shares one
    inline-start edge; the second shares a second, greater one — a distinct
    column, not a third field dropped into the same row. This replaces
    ``test_step_three_controls_share_a_baseline_when_copy_wraps``, which
    pinned the old three-column row's shared *baseline* — a property that
    stopped being true the moment the production total moved under the unit
    instead of beside it.

    Mutation: restoring ``.zones { grid-template-columns: 1fr 1fr 1fr }`` with
    the fields unzoned (the old three-column template) puts ``#total-input`` in
    its own column, equal to neither the amount field's edge nor the money
    pair's, and this test fails.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)

    def edges(ids):
        return [
            round(page.locator(f"#{control_id}").bounding_box()["x"])
            for control_id in ids
            if page.locator(f"#{control_id}").count()
        ]

    mass_edges = edges(["total-waste", "unit-count", "total-unit", "total-input"])
    money_edges = edges(["total-value", "wasted-value"])

    assert max(mass_edges) - min(mass_edges) <= 1, (
        f"the mass fields do not share one inline-start edge: {mass_edges}"
    )
    assert max(money_edges) - min(money_edges) <= 1, (
        f"the money fields do not share one inline-start edge: {money_edges}"
    )
    assert min(money_edges) - max(mass_edges) > 20, (
        "the money group is not a distinct column further along the inline "
        f"axis than the mass group: mass={mass_edges} money={money_edges}"
    )


def test_step_three_the_two_groups_first_labels_share_a_baseline(page_at):
    """The two zones' outer boxes start on one line — they are grid items in a
    single row, so their tops are the row's top whether they stretch to a shared
    height or not. **That alone does not put their *first labels* on one line**,
    which is what this measures: a zone could align perfectly and still open at a
    different height inside its own box. Each zone's own opening rhythm is what
    does it. Both open with an ``h2`` and
    a ``.zone-sub`` of the same size and margins, and then with a
    ``.form-field`` carrying ``.form-field``'s one declared ``25px 0``, so the
    first label of each starts at the same height. The two used to disagree:
    ``.money-fields .form-field`` was ``margin: 16px 0`` against ``25px 0``, from
    when the money pair sat in its own row *below* the primary one, and the 9px
    showed up as one group's first label starting 9px lower than the other's —
    a misalignment that reads as a bug, not a deliberate difference in weight.

    **Rewritten with the zoning change of 2026-09-19, keeping its subject.** It
    used to name ``label[for="total-waste"]`` and ``label[for="total-value"]``
    directly. The unit control has moved *above* the waste amount — it governs
    two quantities on this step, so it leads the column they are in — which
    makes ``label[for="total-unit"]`` the first zone's first label, and a test
    still naming ``total-waste`` would have compared a second label with a
    first and pinned nothing. It asks each zone for its own first label now,
    which is what it was always trying to say and what survives the next
    reordering within a zone.

    This does not require every control to line up: the zones are independent
    vertical flows with three fields and two, and their hints may wrap
    differently further down (see
    ``test_step_three_unit_control_is_narrower_than_its_neighbours`` and the
    comment in ``styles.css`` beside the 650px rule). Only the shared starting
    point is pinned here.

    Mutation: any rhythm that differs between the two zones' openings — giving
    ``.zone > h2`` or ``.zone > .zone-sub`` a different size or margin in one of
    them, or restoring a tighter ``margin`` on the second zone's fields — moves
    one first label off the other's line, and this test fails naming both
    heights.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    tops = page.evaluate(
        """() => [...document.querySelectorAll('.amount-grid .zone')].map(zone => {
             const label = zone.querySelector('label');
             return {
               heading: zone.querySelector('h2')?.textContent?.trim() || null,
               label: label?.textContent?.trim() || null,
               top: label ? Math.round(label.getBoundingClientRect().y) : null,
             };
           })"""
    )
    assert len(tops) == 2, f"step 3 does not render two zones: {tops}"
    assert all(zone["top"] is not None for zone in tops), (
        f"a zone carries no label at all: {tops}"
    )
    assert abs(tops[0]["top"] - tops[1]["top"]) <= 1, (
        f"the two zones' first labels do not share a baseline: {tops}"
    )


def test_step_three_unit_control_is_narrower_than_its_neighbours(page_at):
    """The unit leads the two quantities it governs, and is visibly narrower
    than either — the placement that says "this is the unit of the two fields
    below it", not a third quantity beside them. (It sat *between* them until
    the zoning change of 2026-09-19 moved it to the head of its own zone; the
    assertions are unchanged, because what they pin is the width, and the width
    is what carries the meaning either way.)

    It is deliberately not step 4's ``.amount-with-unit`` compound control.
    There the unit governs one number and can sit beside it; here it governs
    two — the waste amount and the production total — and a control beside one
    of them would read as belonging to that one.

    Mutation: dropping ``.unit-field select { inline-size: 50% }`` back to the
    site-wide ``input, select { width: 100% }`` makes the unit control as wide
    as its neighbours, and this test fails.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    amount_width = page.locator("#total-waste").bounding_box()["width"]
    unit_width = page.locator("#total-unit").bounding_box()["width"]
    total_width = page.locator("#total-input").bounding_box()["width"]

    assert unit_width < amount_width * 0.75, (
        f"the unit control is not visibly narrower than the waste amount field: "
        f"unit={unit_width} amount={amount_width}"
    )
    assert unit_width < total_width * 0.75, (
        f"the unit control is not visibly narrower than the production total field: "
        f"unit={unit_width} total={total_width}"
    )


def test_step_three_unit_select_title_carries_the_full_selected_label(page_at):
    """``#total-unit`` may truncate its closed-state text - the owner's own
    decision, and measured elsewhere to land anywhere from `kilograms` in
    full down to `23 L kerbsi…` depending on viewport width - so `title`
    carries the full text of whichever option is currently selected, for
    hover and assistive technology. It is hover-only and no substitute for
    opening the list, but it should at least say what it claims to.

    Checked in both directions: the default (a weight, never truncated, so
    an easy case to get right by accident) and after switching to a
    container preset (where the label is long enough to actually truncate,
    and where a `title` that was set once at first render and never
    recomputed would go stale).

    Mutation: removing the ``title`` attribute from ``#total-unit`` (the
    pre-fix markup) makes ``get_attribute("title")`` return ``None`` in both
    cases, and both assertions fail.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    select = page.locator("#total-unit")

    def option_text(value):
        return select.locator(f'option[value="{value}"]').text_content()

    assert select.get_attribute("title") == option_text("kilograms"), (
        f"the select's title does not match the selected 'kilograms' option: "
        f"{select.get_attribute('title')!r}"
    )

    preset = select.locator("option").evaluate_all(
        "options => options.map(option => option.value).find(value => value.startsWith('preset:'))"
    )
    assert preset, "step 3 offers no container preset"
    page.select_option("#total-unit", preset)
    page.wait_for_selector("#unit-count")

    assert select.get_attribute("title") == option_text(preset), (
        f"the select's title did not update to the newly selected preset's own label: "
        f"{select.get_attribute('title')!r} != {option_text(preset)!r}"
    )


def test_step_three_is_a_single_column_below_the_breakpoint(page_at):
    """Below ``min-width: 650px`` the two zones stack and every field in both
    shares one inline-start edge — unit, waste amount, total produced, then the
    two NZ$ figures, which is the correct single-column reading order.

    **Rewritten with the zoning change of 2026-09-19, keeping its subject.** It
    used to count ``.amount-grid .form-field`` flat and require *every* adjacent
    gap to be 25px, the mass→money join included. That join was 25px only by
    *margin collapse* between two ordinary block siblings, and a zone is a
    padded, bordered box: padding blocks a margin from escaping it, so the join
    across the two zones cannot collapse any more and is now ``.zones``'s own
    declared ``gap`` instead. Both numbers are pinned here, separately, because
    they are now two different mechanisms:

    * **within a zone, 25px** — ``.form-field``'s own declared ``25px 0``,
      adjoining margins collapsing to one gap exactly as before. This is the
      original assertion, kept, and it is still the one that catches a future
      change that puts these fields back onto a grid and silently doubles the
      gap to 50.
    * **between the zones, 14px** — a stated value on ``.zones``, not an
      accident of collapse.

    **Do not "fix" a doubled gap here by removing the zone's padding.** The
    padding is what makes the zone a visible zone, which is the whole point of
    the change; the separate assertion below is what says so.

    Mutation A: giving ``.zones`` its ``grid-template-columns`` at this width
    splits the second zone's fields onto a second, greater edge, and the first
    assertion fails. Mutation B: ``.zone { display: grid }`` makes each zone's
    fields grid items, where adjoining margins never collapse, and the
    within-zone gaps read 50 instead of 25 — the exact defect this test was
    originally written against, now reproduced one level in. Mutation C: any
    other value for ``.zones``'s ``gap`` fails the third assertion naming both
    numbers.
    """
    page = advance_to(page_at(390, 700, 3.0), 2)

    edges = page.locator(
        "#total-waste, #unit-count, #total-unit, #total-input, #total-value, #wasted-value"
    ).evaluate_all("controls => controls.map(control => Math.round(control.getBoundingClientRect().x))")

    assert max(edges) - min(edges) <= 1, (
        f"step 3 fields do not share one inline-start edge below the breakpoint: {edges}"
    )

    measured = page.evaluate(
        """() => {
          const zones = [...document.querySelectorAll('.amount-grid .zone')];
          const box = el => el.getBoundingClientRect();
          return {
            count: zones.length,
            gaps: zones.map(zone => {
              const fields = [...zone.querySelectorAll('.form-field')].map(box);
              return fields.slice(1).map((f, i) => Math.round(f.top - fields[i].bottom));
            }),
            between: zones.length === 2
              ? Math.round(box(zones[1]).top - box(zones[0]).bottom)
              : null,
          };
        }"""
    )

    assert measured["count"] == 2, (
        f"step 3 does not render two zones below the breakpoint: {measured}"
    )
    flattened = [gap for zone in measured["gaps"] for gap in zone]
    assert flattened and all(abs(gap - 25) <= 1 for gap in flattened), (
        f"adjacent fields inside a zone do not collapse to a single 25px margin "
        f"below the breakpoint: {measured['gaps']}"
    )
    assert abs(measured["between"] - 14) <= 1, (
        f"the two zones are {measured['between']}px apart rather than the 14px "
        f"`.zones` declares: {measured}"
    )


def test_step_three_container_feedback_does_not_overlap_its_error(page_at):
    """The amount field can carry five children at once, not four.

    ``amountStep()`` (web/js/calculator.js) renders the first field as
    label, hint, control, then — in container mode, with a client-side
    validation error showing — *both* ``p.field-error`` and
    ``p.container-total``, in that order. The field's children are ordinary
    block flow inside ``.mass-fields`` (no grid, no subgrid, no row-span
    count), so this is not a track a fifth child can run out of and be
    clamped into — each child simply follows the one before it — but this
    test measures the two paragraphs' own geometry rather than assume the
    absence of a mechanism guarantees the absence of a defect. A prior grid
    based on ``grid-row: span`` did carry exactly this failure mode at one
    and two tracks short of the five the field can render; this test is
    what caught it then and is kept as the direct check now that the
    mechanism has changed.

    Reached from an ordinary path: pick a container preset, then type a
    count that is refused — ``0`` here, ``1`` past ``containerLimit()``
    reaches the same collision by the sibling branch at
    ``validateCurrentStep``'s over-limit check. Both paint
    ``p.field-error`` *and* ``p.container-total`` at once; only one of them
    needs testing here since the clamp is the same defect either way.

    Mutation: giving ``.container-total`` a negative ``margin-block-start``
    large enough to climb back over ``#amount-error`` reproduces the
    original overlap, and this test fails.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)

    preset = page.locator("#total-unit option").evaluate_all(
        "options => options.map(option => option.value).find(value => value.startsWith('preset:'))"
    )
    assert preset, "step 3 offers no container preset"
    page.select_option("#total-unit", preset)
    page.wait_for_selector("#unit-count")
    page.fill("#unit-count", "0")
    press_continue(page)
    page.wait_for_selector("#amount-error")

    error_box = page.locator("#amount-error").bounding_box()
    total_box = page.locator("#container-total").bounding_box()
    assert error_box and total_box, (
        f"container mode did not render both the field error and the container total: "
        f"error={error_box} total={total_box}"
    )
    overlaps = (
        error_box["y"] < total_box["y"] + total_box["height"]
        and total_box["y"] < error_box["y"] + error_box["height"]
    )
    assert not overlaps, (
        "the field error and the container total overlap: "
        f"error={error_box} total={total_box}"
    )


def test_the_production_total_names_its_unit_and_is_cleared_when_the_unit_changes(page_at):
    """`#total-input` is a mass in `state.totalUnit`, and `#total-unit` is the
    control that says which unit that is - so before this, changing the select
    silently reinterpreted whatever was already in the box. Both directions were
    measured on the running stack: 50000 typed against kilograms left as
    `"50000000.000"` once tonnes was chosen, and 50 typed against tonnes left as
    `"50.000"` once a container preset pinned `totalUnit` back to kilograms.

    **Two halves, and neither is sufficient alone.** The field is cleared, the
    same way `state.current` already is and for the same reason - the figure was
    entered against a unit that is no longer in force. And it *names* its unit,
    because a box that says only "Total amount produced" gives a visitor nothing
    to check the number against; `#total-waste` at least sits beside the select
    the visitor just used and is read back on the review step, and this field
    appears on neither screen again.

    Clearing rather than converting is deliberate: converting it would be the
    front end doing arithmetic on the visitor's behalf, on a figure they can no
    longer see, and the destination rows beside it are cleared rather than
    converted already.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    field = page.locator("#total-input")
    label = page.locator('label[for="total-input"]')

    assert "kilograms" in label.inner_text(), (
        f"the field does not name the unit it is read in: {label.inner_text()!r}"
    )
    #: **Issue #65's half of this test: the explanation is on screen, and it is
    #: on screen once.** The two sentences were written on 2026-09-19 but both
    #: lived behind the term tooltip, which a reader has to know is there before
    #: it can help them - and the client's report was that this field is not
    #: explained. So the hint carries *what goes in the box* and the tooltip
    #: carries *why it is optional*, and neither repeats the other: printing
    #: both in both places makes `aria-describedby` read the same two sentences
    #: twice in a row, once as the label's description and once as the hint.
    hint = field.locator("xpath=..").locator(".field-hint").inner_text()
    assert "same period" in hint and "waste included" in hint, (
        f"the production-total hint does not explain what to include: {hint!r}"
    )
    #: `text_content`, not `inner_text`: the tooltip is hidden until the term is
    #: hovered or focused, and `inner_text` reports what is *rendered*, which for
    #: a `visibility: hidden` panel is the empty string. What is being asserted
    #: here is which sentence the markup carries, not whether it is on screen -
    #: `test_step_three_zones_browser.py` owns the opening behaviour.
    tip = label.locator(".term .tip").text_content()
    assert "share of production" in tip and "Leaving it empty" in tip, (
        f"the production-total tooltip does not explain why the field is optional: {tip!r}"
    )
    assert "waste included" not in tip and "share of production" not in hint, (
        "the hint and the tooltip say the same thing, so a screen reader reads it "
        f"twice - hint {hint!r}, tooltip {tip!r}"
    )

    field.fill("50000")
    page.select_option("#total-unit", "tonnes")
    page.wait_for_timeout(120)
    assert page.locator("#total-input").input_value() == "", (
        "50000 entered against kilograms survived the switch to tonnes, where it "
        "means a thousand times as much"
    )
    assert "tonnes" in page.locator('label[for="total-input"]').inner_text(), (
        "the label still names the old unit after the select changed"
    )

    # The other direction, and the one no arithmetic could have rescued: a
    # container pins `totalUnit` back to kilograms, so a figure entered in tonnes
    # would have been read as kilograms with nothing on screen having moved.
    page.fill("#total-input", "50")
    preset = page.locator("#total-unit option").evaluate_all(
        "options => options.map(o => o.value).filter(v => v.startsWith('preset:'))"
    )
    assert preset, "step 3 offers no container preset, so this half cannot be measured"
    page.select_option("#total-unit", preset[0])
    page.wait_for_timeout(120)
    assert page.locator("#total-input").input_value() == "", (
        "50 entered against tonnes survived the switch to a container, which pins "
        "the unit to kilograms"
    )
    assert "kilograms" in page.locator('label[for="total-input"]').inner_text()


def test_the_production_total_is_optional_and_continue_still_works(page_at):
    """The affirmative half. A test that only checks the field exists is
    satisfied by a field that blocks the form."""
    page = advance_to(page_at(1278, 983, 1.25), 2)

    page.fill("#total-waste", "1200")
    #: #total-input deliberately left empty
    press_continue(page)
    page.wait_for_selector(".destination-row", timeout=5000)

    assert page.locator(".destination-row").count() > 0, (
        "an empty production total blocked the step it is optional on"
    )


def test_a_wasted_value_greater_than_the_total_value_is_refused_at_entry(page_at):
    """**The client's own report: a money block reading "wasted share 102.17%".**
    The wasted food is a subset of the food handled, so its value cannot exceed
    the value of the whole - refused here, at the field, rather than only
    printed unclamped on the results page (`moneySummary` in `results.js`
    deliberately does not clamp `wasted_share_percent`, on the theory that a
    contradiction reaching it is the visitor's own typo showing through; this
    is the fix that stops the typo reaching it at all).
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)

    page.fill("#total-waste", "1000")
    page.fill("#total-value", "46.00")
    page.fill("#wasted-value", "47.00")
    press_continue(page)
    page.wait_for_timeout(80)

    assert page.locator("#amount-title").count() == 1, (
        "the contradiction did not keep the visitor on the amount step"
    )
    assert page.locator(".destination-row").count() == 0, (
        "the step advanced despite the wasted value exceeding the total value"
    )
    #: Attached to `#wasted-value` specifically - the figure the message is
    #: actually about - not the waste-amount field, and not a generic banner.
    field = page.locator("#wasted-value")
    assert field.get_attribute("aria-invalid") == "true"
    assert field.get_attribute("aria-describedby") == "wasted-value-error"
    message_el = page.locator("#wasted-value-error")
    assert message_el.count() == 1, "no message was shown against #wasted-value"
    assert message_el.get_attribute("role") == "alert"
    message = message_el.inner_text()
    assert "waste" in message.lower() and "production" in message.lower(), (
        f"the message does not say which figure is the problem: {message!r}"
    )
    #: Not also duplicated against the waste-amount field, which this
    #: contradiction is not about.
    assert page.locator("#amount-error").count() == 0, (
        "the money contradiction was ALSO attached to the waste-amount field"
    )

    #: The affirmative half: pulling the wasted figure back under the total
    #: lets the visitor continue, exactly as editable as it always was.
    page.fill("#wasted-value", "45.00")
    press_continue(page)
    page.wait_for_selector(".destination-row", timeout=5000)
    assert page.locator(".destination-row").count() > 0, (
        "a wasted value under the total was still refused"
    )


def test_a_waste_amount_greater_than_the_production_total_is_refused_at_entry(page_at):
    """**The same contradiction, one dimension over.** The waste amount is a
    subset of the production total beside it, so the same rule applies to the
    two masses as to the two money figures above - and the same server-side
    ratio (`production_share_percent`, §4.6) would otherwise print a share
    past 100% for the same reason the money share could.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)

    page.fill("#total-waste", "1500")
    page.fill("#total-input", "1000")
    press_continue(page)
    page.wait_for_timeout(80)

    assert page.locator("#amount-title").count() == 1, (
        "the contradiction did not keep the visitor on the amount step"
    )
    assert page.locator(".destination-row").count() == 0, (
        "the step advanced despite the waste amount exceeding the production total"
    )
    #: Attached to the waste-amount field itself - the mass contradiction's
    #: own subject, unlike the money one above.
    field = page.locator("#total-waste")
    assert field.get_attribute("aria-invalid") == "true"
    assert field.get_attribute("aria-describedby") == "amount-error"
    message_el = page.locator("#amount-error")
    assert message_el.count() == 1, "no message was shown against #total-waste"
    assert message_el.get_attribute("role") == "alert"
    message = message_el.inner_text()
    assert "waste" in message.lower() and "produced" in message.lower(), (
        f"the message does not say which figure is the problem: {message!r}"
    )

    #: The affirmative half.
    page.fill("#total-input", "2000")
    press_continue(page)
    page.wait_for_selector(".destination-row", timeout=5000)
    assert page.locator(".destination-row").count() > 0, (
        "a waste amount under the production total was still refused"
    )


@pytest.mark.parametrize(
    "total_value,wasted_value",
    [
        pytest.param("", "47.00", id="value_handled_blank"),
        pytest.param("46.00", "", id="value_wasted_blank"),
        pytest.param("", "", id="both_blank"),
    ],
)
def test_a_blank_money_field_is_exempt_from_the_contradiction_check(page_at, total_value, wasted_value):
    """**Both money fields are optional by design (§4.5), and the mass side of
    this same round already has its own test for this
    (`test_the_production_total_is_optional_and_continue_still_works`); the
    money side had none.** `moneyContradictionValidation` returns early on a
    blank `#total-value` or `#wasted-value` - untested, that guard could be
    deleted and the whole file would still pass while a blank optional field
    was refused outright, exactly the `not_supplied`/`incomplete` regression
    the four-state `data_state` model at v1.51 exists to keep the calculator
    out of.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)

    page.fill("#total-waste", "1000")
    if total_value:
        page.fill("#total-value", total_value)
    if wasted_value:
        page.fill("#wasted-value", wasted_value)
    press_continue(page)
    page.wait_for_selector(".destination-row", timeout=5000)

    assert page.locator(".destination-row").count() > 0, (
        f"a blank money field (total={total_value!r}, wasted={wasted_value!r}) "
        "was refused as though it contradicted the other"
    )


@pytest.mark.parametrize(
    "total_value,wasted_value,should_refuse",
    [
        pytest.param("47.00", "47.00", False, id="exactly_equal_at_the_cent"),
        pytest.param("47.00", "47.01", True, id="one_cent_over"),
        #: These two logical gaps are identical - one cent - and used to fall on
        #: opposite sides of `ALLOCATION_EPSILON` purely from binary floating-point
        #: error (`0.04 - 0.03` and `0.08 - 0.07` land on different sides of `0.01`
        #: in a double). Both must now be refused, identically.
        pytest.param("0.03", "0.04", True, id="one_cent_over_dust_prone_low"),
        pytest.param("0.07", "0.08", True, id="one_cent_over_dust_prone_high"),
        pytest.param("46.00", "47.00", True, id="the_clients_own_figures"),
    ],
)
def test_the_money_contradiction_is_decided_at_the_exact_cent_not_by_float_dust(
    page_at, total_value, wasted_value, should_refuse
):
    """**Finding 1.** `exceedsTotal`'s `ALLOCATION_EPSILON` (0.01) is a mass
    tolerance, built for a scale that does not agree with itself to the gram.
    Reused as a money rule it let a wasted value up to a whole cent over its
    own total through - the client's own defect, one cent smaller - and even
    that one-cent boundary was decided by double-precision rounding rather
    than by the figure actually typed. `moneyCents` parses both figures
    directly into integer cents, so the decision cannot be moved by dust: the
    exact-cent boundary (equal values) is allowed, and every one-cent-over case
    here is refused identically regardless of which specific figures produce
    the one-cent gap.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)

    page.fill("#total-waste", "1000")
    page.fill("#total-value", total_value)
    page.fill("#wasted-value", wasted_value)
    press_continue(page)
    page.wait_for_timeout(80)

    advanced = page.locator(".destination-row").count() > 0
    if should_refuse:
        assert not advanced, (
            f"wasted value {wasted_value} against total value {total_value} was allowed through"
        )
    else:
        assert advanced, (
            f"wasted value {wasted_value} against total value {total_value} was refused"
        )


def test_the_money_contradiction_is_checked_per_entry_not_across_the_whole_submission(page_at):
    """**Confirms this stayed true through the fix.** The check reads only the
    draft entry's own two fields, never anything saved on an earlier entry - so
    a submission whose figures would sum to something unobjectionable can
    still be refused, if one entry's own pair contradicts.

    Entry 1: value 1000 / wasted 10 (saved, unremarkable). Entry 2, the draft:
    value 5 / wasted 500 - refused on its own even though 1005 handled against
    510 wasted, summed across both entries, would not be.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    page.fill("#total-waste", "1000")
    page.fill("#total-value", "1000")
    page.fill("#wasted-value", "10")
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    page.fill('[data-line-field="amount"] >> nth=0', "1000")
    page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('[data-action="add-entry"]')

    page.click('[data-action="add-entry"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    press_continue(page)
    page.wait_for_selector("#total-waste")

    page.fill("#total-waste", "500")
    page.fill("#total-value", "5")
    page.fill("#wasted-value", "500")
    press_continue(page)
    page.wait_for_timeout(80)

    assert page.locator(".destination-row").count() == 0, (
        "the second entry's own money contradiction was masked by the first entry's figures"
    )
    message = page.locator("#wasted-value-error").inner_text()
    assert "495.00" in message, f"unexpected excess figure: {message!r}"


def test_a_blocked_refusal_still_shows_a_message_back_on_the_amount_step(page_at):
    """**A live regression, not a new requirement.** `amountStep`'s three-way
    classification (`amountFieldError` / `moneyContradictionError` /
    `bannerError`) matches `state.error` against whichever of
    `amountOnlyValidation`, `moneyContradictionValidation` and
    `massContradictionValidation` currently agrees with it - and, before this
    fix, had no branch for an error that matches none of them.

    `BLOCKED` is exactly that state. §9.2: a blocked caller must never be
    offered a "try again" affordance, so `clearedError` deliberately keeps a
    `BLOCKED` error and its `errorCode` across a step change. A visitor refused
    at Calculate (step 4) who then returns to step 2 carries an error that is
    not a `VALIDATION_ERROR` and satisfies none of the three client checks
    either - so it matched nothing and rendered nothing. Measured at HEAD
    before this fix: no visible message at all, where the previous single-slot
    code showed the refusal text.
    """
    page = advance_to(page_at(1278, 983, 1.25), 4)
    page.route(
        "**/api/v1/calculate",
        lambda route: route.fulfill(
            status=403,
            content_type="application/json",
            body=json.dumps(
                {
                    "error": {
                        "code": "BLOCKED",
                        "message": "This submission was refused.",
                        "details": None,
                    }
                }
            ),
        ),
    )
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_timeout(200)

    #: §9.2's own affordance rule, restated as a precondition: the refusal is
    #: terminal, so Calculate must already be disabled on the screen it landed
    #: on, before this test ever asks about step 2.
    assert page.locator('.step-nav [data-action="calculate"]').is_disabled(), (
        "a BLOCKED refusal left Calculate enabled, offering a retry §9.2 forbids"
    )

    page.click('[data-action="go-step"][data-step="2"]')
    page.wait_for_selector("#total-waste")

    messages = [
        text.strip()
        for text in page.locator(".field-error").all_inner_texts()
        if text.strip()
    ]
    assert messages, (
        "a refused submission shows no visible message at all back on the amount step"
    )
    assert any("refused" in message.lower() for message in messages), messages


def test_step_three_asks_for_the_two_money_figures(page_at):
    """Item ⑤. Both optional, both in New Zealand dollars, and both
    STATISTICS ONLY - the client's ruling on open item O-2 is that the value
    of the food does not enter the main formula and that cost price versus
    retail price is their own client's question.

    The currency is in the label rather than in a symbol beside the box: a
    bare `$` is ambiguous across the twenty languages this ships in, and the
    figure is only ever NZD.
    """
    #: `2`, not the UI's own "Step 3" label - see the comment on
    #: `test_step_three_asks_what_the_stage_put_through` above, which is the
    #: same amount screen these two fields join.
    page = advance_to(page_at(1278, 983, 1.25), 2)

    for field_id in ("total-value", "wasted-value"):
        field = page.locator(f"#{field_id}")
        assert field.count() == 1, f"no #{field_id}"
        # `count() == 1` is satisfied by a field that is `display: none` or
        # `disabled` just as readily as by one a visitor can actually use -
        # Task 1's own reviewer flagged exactly this gap. A hidden or
        # non-editable field never reaches `beforeinput`, never reaches
        # `state`, and never reaches the wire; the field has to be usable,
        # not merely present, and typing into it and reading the value back
        # is the only check that tells the difference.
        assert field.is_visible(), f"{field_id} exists but is not visible"
        assert field.is_editable(), f"{field_id} exists but cannot be typed into"
        field.fill("42")
        assert field.input_value() == "42", f"{field_id} did not keep a typed value"

        label = page.locator(f'label[for="{field_id}"]').inner_text()
        assert "optional" in label.lower(), f"{field_id} is not marked optional"
        assert "NZ$" in label or "NZD" in label, (
            f"{field_id} does not say which currency: {label!r}"
        )

    #: **Which sentence, and not merely that there is one.** "total value" is the
    #: clarification #147 is about and both hints have to keep it - which is
    #: exactly why asserting only that is not enough: the substring is in both,
    #: so swapping the two hints left both assertions green. Each field is
    #: therefore pinned by something only its own sentence says. The production
    #: hint is the one that says what *you produced* and rules a per-unit figure
    #: out; the waste hint is the one that names *the waste* and the basis it
    #: shares with *production above*, which is the §4.5 constraint behind
    #: `wasted_share_percent` and may not retreat into the hover tooltip.
    #:
    #: `label[for=…] + .field-hint` is exact rather than approximate: `leafPanel`
    #: emits `</label><p class="field-hint">` with no node in between.
    #:
    #: **Both must also say which food the figure is for.** `leafPanel` draws one
    #: panel per leaf and `engine/calculate.py` sums the per-entry figures, so a
    #: hint phrased as a whole-business total is a hint a visitor with three food
    #: types types three times - and the results page then shows three times the
    #: true value.
    hints = {
        field_id: page.locator(f'label[for="{field_id}"] + .field-hint').inner_text().lower()
        for field_id in ("total-value", "wasted-value")
    }
    for field_id, hint in hints.items():
        assert "total value" in hint, f'{field_id} hint does not say "total value": {hint!r}'
        assert "this food type" in hint, (
            f"{field_id} hint is not scoped to the card that holds it: {hint!r}"
        )
    production, waste = hints["total-value"], hints["wasted-value"]
    assert "you produced" in production and "per unit" in production, (
        "total-value carries the wrong sentence - it should be the production hint, "
        f"which says what you produced and rules out a per-unit figure: {production!r}"
    )
    assert "production above" not in production, (
        f"total-value carries the waste field's sentence: {production!r}"
    )
    assert "the waste" in waste and "production above" in waste, (
        "wasted-value carries the wrong sentence - it should be the waste hint, "
        f"which names the waste and the basis it shares with production above: {waste!r}"
    )
    assert "per unit" not in waste, (
        f"wasted-value carries the production field's sentence: {waste!r}"
    )


@pytest.mark.parametrize("field_id", ["total-input", "total-value", "wasted-value"])
def test_a_negative_money_figure_is_refused_as_it_is_typed(page_at, field_id):
    """The same guard `#total-waste` and `#unit-count` already have.

    A negative value would reach stage one's `ge=0` and come back a 400 for
    the whole submission, after the visitor had left the screen the figure was
    on. The minus is refused at `beforeinput`, which is where the other two
    refuse it.

    All three of step 3's optional figures, for the reason the decimal test
    above gives: the guard names them one by one, so a test that asks about one
    of them says nothing about the others.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    field = page.locator(f"#{field_id}")

    field.press_sequentially("-500")

    assert field.input_value() == "500", (
        f"a minus reached #{field_id}: {field.input_value()!r}"
    )


def test_the_review_step_asks_what_period_the_figures_cover(page_at):
    """Item ⑦, and it belongs on step 5 rather than step 3.

    The client asked for it "在计算第六步出结果之前" - before the results. It
    is a statement about the whole submission, not about one supply-chain
    stage, so it sits with the review of everything rather than inside the
    per-entry loop where a visitor with three entries would be asked three
    times.

    The values are the five §6.2 accepts. A sixth would be refused by the API
    after the visitor pressed Calculate.

    **`custom` is the fifth, from v1.67.** It is the option that reveals the
    date-and-time fields; the other four now fill them as templates and go on
    recording which shortcut was pressed.
    """
    #: `4`, not the UI's own "Step 5" label - `walk()`'s numeric yields are
    #: one behind the 1-based on-screen label; `4` is the review screen,
    #: confirmed by what it waits for: `[data-action="calculate"]`.
    page = advance_to(page_at(1278, 983, 1.25), 4)

    select = page.locator("#time-frame")
    assert select.count() == 1, "the review step has no period selector"
    # `count() == 1` alone is satisfied by a hidden or disabled selector just
    # as readily as by one a visitor can actually use.
    assert select.is_visible(), "#time-frame exists but is not visible"
    assert select.is_enabled(), "#time-frame exists but cannot be used"

    values = select.locator("option").evaluate_all(
        "options => options.map(o => o.value)"
    )
    assert values == ["", "one_week", "one_month", "one_quarter", "one_year", "custom"], (
        f"the period vocabulary does not match what the API accepts: {values}"
    )

    # The field scales nothing - no figure is annualised, divided or multiplied
    # by it - and a visitor who picks "one week" has no way to know that from
    # the label alone. The natural assumption runs the other way, so the hint
    # carries the fact the label cannot.
    #: `>` rather than a descendant selector: v1.67's period fields carry a
    #: second `.field-hint` (the `dd/mm/yyyy` format note) inside this same
    #: block whenever a period is stated, and a descendant match would count
    #: both and fail on a screen that is working correctly.
    hint = page.locator(".time-frame-field > .field-hint")
    assert hint.count() == 1, "the period selector has no explanatory hint"
    assert hint.is_visible(), "the period hint exists but is not visible"
    assert "result" in hint.inner_text().lower(), (
        "the period hint does not say it leaves the results unchanged"
    )


def test_the_period_is_optional_and_calculate_still_works(page_at):
    """Optional, like the other three. The empty option is first and
    selected, and leaving it there must not block the button."""
    page = advance_to(page_at(1278, 983, 1.25), 4)

    assert page.locator("#time-frame").input_value() == ""
    assert page.locator('.step-nav [data-action="calculate"]').is_enabled()


def test_the_four_new_values_reach_the_request_body(page_at):
    """**The assertion that the fields are wired to something.**

    Every test in Tasks 1-3 proves a control exists and holds a value. None
    of them proves the value leaves the browser, and a field bound to state
    that `submitCalculation` never reads is the most likely way this ships
    half-done - it looks right on every screen.

    The POST is intercepted rather than allowed through, so this measures
    what the front end sends rather than what the API tolerates.
    """
    page = page_at(1278, 983, 1.25)
    sent = {}
    page.route(
        "**/api/v1/calculate",
        lambda route: (sent.update(route.request.post_data_json), route.abort()),
    )

    #: `#total-input`, `#total-value` and `#wasted-value` live on the amount step
    #: (`walk()`'s `2`), not the review step (`4`) where `#time-frame` and the
    #: Calculate button are - so `walk()` is driven directly, rather than through
    #: `advance_to`, to fill each set of fields on the screen that actually carries it.
    for arrived in walk(page):
        if arrived == 2:
            #: **Tonnes, and that is the whole point of this line.** Filled while the
            #: entry unit was kilograms, the conversion on `total_input_kg` is the
            #: identity - so deleting it outright left this test green while claiming in
            #: its own comment to assert the conversion happened. 5000 tonnes is
            #: 5,000,000 kg and no other reading of the field produces that number. The
            #: unit is selected before the field is filled because changing it clears
            #: the field.
            #:
            #: **Larger than `#total-waste`, which `walk()`'s own step-2 fill sets to
            #: "1000" in whatever unit is active - here, tonnes - immediately after
            #: this block runs.** A production total smaller than that would trip the
            #: item-①-round-two guard in `validateCurrentStep` (waste cannot exceed
            #: production) and refuse to advance past step 2 at all, which is a
            #: different test's subject, not this one's.
            page.select_option("#total-unit", "tonnes")
            page.wait_for_timeout(80)
            page.fill("#total-waste", "1")
            page.fill("#total-input", "5000")
            page.fill("#total-value", "120000")
            page.fill("#wasted-value", "4500")
        elif arrived == 4:
            page.select_option("#time-frame", "one_month")
            page.click('.step-nav [data-action="calculate"]')
            page.wait_for_timeout(400)
            break

    assert sent, "no request was made"
    assert sent["time_frame"] == "one_month"
    entry = sent["entries"][0]
    #: 5000 tonnes is 5,000,000 kg. §1.2: decimals travel as strings because
    #: JavaScript's Number is a double.
    #:
    #: **Exactly `"5000000"`, with no invented decimal places.** The send path
    #: ended in `.toFixed(3)`, which also *rounded* - a typed `1.2345` became
    #: `"1.234"`, the very rewrite the round-one fix refused to perform on the
    #: money fields two lines below. The two families now apply one rule.
    assert entry["total_input_kg"] == "5000000"
    #: The two money fields are **not** reformatted - Fix round 1 found
    #: `Number(value).toFixed(2)` silently padding (and, for a third typed
    #: decimal, rounding) a figure nobody asked to have rewritten. "120000"
    #: and "4500" are exactly what was typed, and that is what must arrive.
    assert entry["total_value_nzd"] == "120000"
    assert entry["wasted_value_nzd"] == "4500"

    #: The front-end half of the agreement stage one's `test_evaluator.py`
    #: runs on the engine side: two independent pictures of one contract,
    #: and nothing but a test stops them drifting. `route.abort()` above
    #: proves the front end sends *a* shape; running the real Pydantic model
    #: over the captured body is what proves it sends *the* shape - cheaply,
    #: in CI, with no container and no network call.
    CalculatePayload.model_validate(sent)


def test_an_untouched_field_is_sent_as_null_rather_than_zero(page_at):
    """`None` and `0` are different claims, and stage one's schema keeps them
    apart. A front end that sent `"0"` for an empty box would make every
    visitor claim they produced nothing and wasted nothing."""
    page = page_at(1278, 983, 1.25)
    sent = {}
    page.route(
        "**/api/v1/calculate",
        lambda route: (sent.update(route.request.post_data_json), route.abort()),
    )

    page = advance_to(page, 4)
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_timeout(400)

    entry = sent["entries"][0]
    assert entry["total_input_kg"] is None
    assert entry["total_value_nzd"] is None
    assert entry["wasted_value_nzd"] is None
    assert sent["time_frame"] is None

    #: The absent shape agrees with §6.2 too - `null` on all four optional
    #: fields is what `CalculatePayload` accepts, not merely what this test
    #: asserts about it.
    CalculatePayload.model_validate(sent)


@pytest.mark.parametrize("field_id", ["total-value", "wasted-value"])
def test_a_third_decimal_in_a_money_field_is_refused_as_it_is_typed(page_at, field_id):
    """The same keystroke-level refusal `#wasted-value` already gives a
    minus sign, aimed at the decimal point instead of the sign.

    Fix round 1: `submitCalculation` used to call `Number(value).toFixed(2)`,
    so a visitor who typed "12.345" silently sent "12.35" - a figure they
    never wrote down. Rounding what already arrived is not an option §7.6.1
    allows (it is a calculation, and the front end's only permitted one is a
    unit conversion), so the third decimal has to be refused as it is typed,
    the way the minus already is - and then whatever is left must reach the
    wire completely unrounded, which `test_the_four_new_values_reach_the_
    request_body` is what checks.

    **Both money fields, because one was not enough.** Measured only against
    `#wasted-value`, a mutation narrowing the guard to that single id survived
    the whole file - and there is nothing about the guard that makes the two
    fields move together except a test that asks them both.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    field = page.locator(f"#{field_id}")

    field.press_sequentially("12.345")

    assert field.input_value() == "12.34", (
        f"a third decimal place reached #{field_id}: {field.input_value()!r}"
    )


def test_a_fourth_decimal_in_the_production_total_is_refused_as_it_is_typed(page_at):
    """`total_input_kg` is `DECIMAL(16,3)` (§6.2), so its ceiling is three rather
    than the money fields' two - and until now it had no ceiling at all while its
    send path silently rounded, which is the two families applying opposite rules
    to the same mistake.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    field = page.locator("#total-input")

    field.press_sequentially("1.2345")

    assert field.input_value() == "1.234", (
        f"a fourth decimal place reached the production total: {field.input_value()!r}"
    )
    #: And three are still typeable. A guard that refused the third as well would
    #: pass the assertion above while quietly imposing the money ceiling here.
    assert len(field.input_value().split(".")[1]) == 3
    #: The control has to declare the same granularity the guard enforces. It
    #: said `step="0.01"` - the money fields' - so the browser called the third
    #: decimal place invalid on a field whose contract column is DECIMAL(16,3),
    #: and the spinner stepped in hundredths of a kilogram.
    assert field.get_attribute("step") == "0.001", (
        "the production total declares a granularity its own guard does not enforce: "
        f"{field.get_attribute('step')!r}"
    )


def test_the_production_total_is_not_rounded_on_its_way_to_the_wire(page_at):
    """What the visitor typed is what is sent, in kilograms and in tonnes alike.

    `page.fill` hands the whole string over in one `beforeinput`, which the
    keystroke guard above deliberately does not count against the ceiling (see
    the note beside it: counting every digit of a six-digit fill refused an
    ordinary whole-number entry). That makes `fill` the way to ask what the send
    path does with a figure the guard never saw - and the answer used to be
    `.toFixed(3)`, which rounded `1.2345` to `1.234`.

    **`CalculatePayload.model_validate` is deliberately not run on the kilogram
    body.** `5000.1234` kg is four decimal places, so §6.2 refuses it - and that
    is the honest outcome the round-one money fix chose over rewriting the
    figure: the ceiling is enforced at the keystroke, and anything that gets
    past it goes to the server as written rather than being quietly made
    acceptable. The tonnes body is valid and is checked.

    **The production figure is larger than the waste amount, on purpose.**
    `walk()`'s own step-2 fill (`#total-waste` -> `"1000"`) runs immediately
    after this function's own custom fill, in whatever unit `#total-unit` was
    just set to - so the waste amount here is always "1000" in that unit,
    regardless of which branch is under test. A production total *smaller*
    than that would trip the item-①-round-two guard added to
    `validateCurrentStep` (waste cannot exceed production, the mass-dimension
    twin of the money contradiction the client reported), which refuses to
    advance past step 2 at all - and this test is about what reaches the wire,
    not about that refusal.
    """

    def sent_body(unit, typed):
        page = page_at(1278, 983, 1.25)
        body = {}
        page.route(
            "**/api/v1/calculate",
            lambda route: (body.update(route.request.post_data_json), route.abort()),
        )
        for arrived in walk(page):
            if arrived == 2:
                page.select_option("#total-unit", unit)
                page.wait_for_timeout(80)
                page.fill("#total-waste", "1000" if unit == "kilograms" else "1")
                page.fill("#total-input", typed)
            elif arrived == 4:
                page.click('.step-nav [data-action="calculate"]')
                page.wait_for_timeout(400)
                break
        assert body, "no request was made"
        return body

    kilograms = sent_body("kilograms", "5000.1234")
    assert kilograms["entries"][0]["total_input_kg"] == "5000.1234", (
        "the production total was rounded on its way to the wire"
    )

    tonnes = sent_body("tonnes", "5000.1234")
    #: 5000.1234 t is 5,000,123.4 kg exactly - the conversion gains three
    #: decimal places, so nothing is rounded here either, and the result is
    #: inside §6.2's three.
    assert tonnes["entries"][0]["total_input_kg"] == "5000123.4"
    CalculatePayload.model_validate(tonnes)


def test_a_slider_cannot_be_dragged_past_what_is_left(page_at):
    """**Item ⑨, and the rule it works within does not change - only WHERE it is
    enforced.**

    `improvementValidation` requires the allocation to total exactly 100% -
    that is what keeps both scenarios moving the same mass, so net benefit
    cannot be inflated by assuming less waste in the alternative. The client
    confirmed it stands. What is wrong is only that a slider will happily go
    past the remaining headroom and leave the visitor to notice.

    **Every slider's `max` is now FIXED (see `fixedRowMax` in
    `web/js/improvement.js`) - `100` in percentage mode, regardless of any
    other row's value.** It is no longer this row's own value plus whatever is
    unallocated: that was the earlier mechanism, and shrinking a max was what
    moved an untouched row's THUMB even when its value did not (item ⑨ round
    two's own report). The 100%-total rule is enforced instead on the value
    being entered, in `updateImprovementInput`'s own ceiling clamp - so pulling
    the first destination to 100% leaves the second UNABLE TO BE INCREASED
    (an attempt to drag it up clamps straight back to what it already held),
    while its `max` attribute never moves off the fixed `100` at all.
    """
    page = _improvement_panel(page_at)
    sliders = page.locator('input[type="range"][data-improvement-code]')
    assert sliders.count() >= 2, "need two destinations to test headroom"

    max_before = sliders.nth(1).get_attribute("max")

    #: Everything to the first destination.
    sliders.nth(0).evaluate("el => { el.value = '100'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    assert sliders.nth(1).get_attribute("max") == max_before == "100", (
        f"the second slider's own max moved off its fixed figure: {max_before!r} -> "
        f"{sliders.nth(1).get_attribute('max')!r}"
    )

    #: The second destination has no headroom left (the first took it all) -
    #: dragging it up must be refused, even though nothing shrank its `max`.
    sliders.nth(1).evaluate("el => { el.value = '50'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    assert sliders.nth(1).input_value() == "0", (
        f"the second slider still offers headroom that does not exist: {sliders.nth(1).input_value()!r}"
    )
    assert page.locator("#improvement-total-value").inner_text() == "100.00%"


def test_dragging_one_destination_does_not_move_another(page_at):
    """**The client's own report:** "我推动一个剩下的几个会跟着进退" - dragging
    ReFED Donations from 1.70 to 3.20 kg made ReFED Prevention fall from 7.80
    to 2.80 kg on its own.

    Reproduced by the route that actually triggers it: `sliderMax` recomputed
    every OTHER destination's `max` from a shared `headroom`, and assigning a
    `max` below an `<input type="range">`'s current `value` silently clamps
    that value - so a destination nobody touched changed. Six destinations are
    set up close to 100% (as a visitor typing several figures in a row would
    arrive at), then ONE is pushed past the 0.10% actually left; every OTHER
    destination's own figure, box and slider both, must read exactly what it
    read before.

    Confirmed against the real defect first: before this fix, this same drag
    left ReFED Prevention's slider reading `6.4` (its box stayed at `7.80`,
    which is itself the two-controls-disagreeing half of the same defect).

    **Superseded as a mutation target by item ⑨ round two's fixed `max`** (see
    `fixedRowMax` in `web/js/improvement.js`): a headroom-derived ceiling no
    longer exists at all, so there is no `sliderMax` body left to restore here.
    `test_decreasing_one_destination_leaves_every_other_row_s_thumb_position_unchanged`
    is what a reintroduced headroom-derived `max` is confirmed to fail against.
    This test still stands on its own: an untouched row's stored VALUE, on
    both controls, must survive a sibling's edit regardless of which mechanism
    enforces the 100% cap.
    """
    page = _improvement_panel(page_at)
    boxes = page.locator('.percentage-input input[type="number"]')
    sliders = page.locator('input[type="range"][data-improvement-code]')
    assert boxes.count() >= 6, "need six destinations to set up a tight allocation"

    #: prevention, refed_prevention, refed_donations, refed_animal_feed,
    #: refed_composting, refed_anaerobic_digestion, in the taxonomy's own
    #: sort order - summing to 99.90%, leaving 0.10% of headroom.
    for index, value in enumerate(["20", "7.80", "1.70", "30", "25", "15.4"]):
        boxes.nth(index).fill(value)
        page.wait_for_timeout(40)

    total_before = page.locator("#improvement-total-value").inner_text()
    assert total_before == "99.90%", f"the setup no longer leaves 0.10% headroom: {total_before!r}"

    other_box_before = boxes.nth(1).input_value()
    other_range_before = sliders.nth(1).input_value()
    assert other_box_before == "7.80"

    #: The push that reproduces the client's report: destination index 2
    #: ("1.70") is asked for 3.20, 1.50 more than the 0.10% actually left.
    boxes.nth(2).fill("3.20")
    page.wait_for_timeout(80)

    assert boxes.nth(1).input_value() == other_box_before, (
        f"an untouched destination's box changed from {other_box_before!r} to "
        f"{boxes.nth(1).input_value()!r} when a DIFFERENT destination was edited"
    )
    assert sliders.nth(1).input_value() == other_range_before, (
        f"an untouched destination's SLIDER changed from {other_range_before!r} to "
        f"{sliders.nth(1).input_value()!r} when a DIFFERENT destination was edited - "
        "this is the exact shape of the client's report even when the box beside it "
        "does not move"
    )


def test_decreasing_one_destination_leaves_every_other_row_s_thumb_position_unchanged(page_at):
    """**The client's report, again, after round two's own fix shipped:**
    dragging ReFED Donations down from 0.12 to 0.03 left the other rows'
    NUMBER BOXES correct (0.052 and 0.53 unchanged) but visibly moved their
    slider THUMBS.

    **Why round two's fix was only half of it.** It floored `sliderMax` at
    each row's own current value, which stopped the browser's silent
    "assigning a lower `max` clamps `.value`" from firing against an
    untouched row - so a sibling's stored VALUE was safe. But `max` itself was
    still recomputed from the remaining headroom on every keystroke, and a
    thumb's rendered position is `value / max`. DECREASING one destination
    GROWS the headroom, which grows every OTHER row's `max`, which moves their
    thumb left even though their stored value never changed at all - "I drag
    one and the others move" survives even once the value itself is
    protected, which is exactly what the client saw a second time.

    So this asserts the thumb POSITION (`value / max`) for every untouched
    row, not only its value - the gap the previous round's own test
    (`test_dragging_one_destination_does_not_move_another`, which asserts
    value only) left open.

    Mutation to confirm this test would catch the regression: give
    `fixedRowMax` back a headroom-derived body -
    `Math.max(typed(value), Math.round((typed(value) + headroom) * 100) / 100, 0)`,
    reading a fresh `headroom = 100 - allocationTotal(state.improvedAllocations)`
    and the row's OWN current value at each `updateImprovementInput` call, the
    way `sliderMax` used to - and watch the THUMB-POSITION assertion below
    fail while the plain value assertion right above it still passes.
    """
    page = _improvement_panel(page_at)
    boxes = page.locator('.percentage-input input[type="number"]')
    sliders = page.locator('input[type="range"][data-improvement-code]')
    assert boxes.count() >= 3, "need three destinations for two to hold still while one moves"

    #: prevention, refed_prevention, refed_donations, in the taxonomy's own
    #: sort order (see the other tests in this module) - 5.20 / 53.00 / 12.00,
    #: comfortably under 100%, so there is real headroom for a decrease to grow.
    boxes.nth(0).fill("5.20")
    boxes.nth(1).fill("53.00")
    boxes.nth(2).fill("12.00")
    page.wait_for_timeout(80)

    def snapshot():
        return sliders.evaluate_all("els => els.map(el => ({value: el.value, max: el.max}))")

    before = snapshot()

    #: The push DOWN the client actually reported: donations falls, so the
    #: headroom every OTHER row could grow into gets BIGGER, not smaller.
    boxes.nth(2).fill("3.00")
    page.wait_for_timeout(80)

    after = snapshot()

    for index in (0, 1):
        assert after[index]["value"] == before[index]["value"], (
            f"row {index}'s stored value changed from {before[index]['value']!r} to "
            f"{after[index]['value']!r} when a DIFFERENT row was decreased"
        )
        assert after[index]["max"] == before[index]["max"], (
            f"row {index}'s own max changed from {before[index]['max']!r} to "
            f"{after[index]['max']!r} when a DIFFERENT row was decreased - a fixed max "
            "must never move for an edit made elsewhere"
        )
        thumb_before = float(before[index]["value"]) / float(before[index]["max"])
        thumb_after = float(after[index]["value"]) / float(after[index]["max"])
        assert abs(thumb_before - thumb_after) < 1e-9, (
            f"row {index}'s THUMB moved from {thumb_before} to {thumb_after} even though "
            "its own value and max both read the same figure before and after - this is "
            "the client's own report: the picture moved even though the number did not"
        )


def test_a_clamp_on_drag_still_agrees_with_the_donut_the_total_and_the_validation_message(page_at):
    """**Item ⑨'s ceiling clamp changes `state.improvedAllocations`, and every
    other view of that same state has to agree with it** - the running total,
    the donut chart and the inline validation message are all re-derived from
    the clamped figure inside `updateImprovementInput`, on the same keystroke;
    this asserts they actually do, rather than trusting that a shared code
    path keeps them in step.
    """
    page = _improvement_panel(page_at)
    boxes = page.locator('.percentage-input input[type="number"]')
    sliders = page.locator('input[type="range"][data-improvement-code]')

    boxes.nth(0).fill("60")
    boxes.nth(1).fill("40")
    page.wait_for_timeout(80)

    #: Dragging the first destination up to 100 clamps straight back to 60 -
    #: see `test_no_destination_can_be_increased_once_the_allocation_reaches_one_hundred_percent`.
    sliders.nth(0).evaluate("el => { el.value = '100'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    assert sliders.nth(0).input_value() == boxes.nth(0).input_value() == "60"
    assert page.locator("#improvement-total-value").inner_text() == "100.00%"

    #: The donut's own slice carries the SAME share the box and slider agree
    #: on, read from the `<title>` its own slice draws. An SVG `<title>` has
    #: no rendered box, so `inner_text()` reads it as empty/`None` - `evaluate_all`
    #: over `.textContent` is what actually reads it.
    pie_titles = page.locator(".improvement-pie-content svg title").evaluate_all(
        "els => els.map(el => el.textContent)"
    )
    assert any("60.0%" in title for title in pie_titles), (
        f"the donut does not show the clamped 60% share anywhere: {pie_titles!r}"
    )
    #: The allocation is exactly 100%, so the inline message is gone and
    #: Compare is enabled - the clamp did not leave the panel thinking the
    #: total is still wrong.
    assert page.locator("#improvement-inline-error").is_hidden()
    assert page.locator('[data-action="compare-improvement"]').is_enabled()


def test_reducing_a_slider_is_never_clamped(page_at):
    """**The clamp is a ceiling, never a floor, and it must not fire on the
    way down.** `updateImprovementInput`'s ceiling check only ever tightens a
    value that would push the total over 100%; a visitor pulling a slider
    DOWN is moving further from that ceiling on every row, on both the row
    being dragged and (per the two tests above) every other row's thumb,
    which must not so much as twitch.
    """
    page = _improvement_panel(page_at)
    boxes = page.locator('.percentage-input input[type="number"]')
    sliders = page.locator('input[type="range"][data-improvement-code]')

    boxes.nth(0).fill("60")
    boxes.nth(1).fill("40")
    page.wait_for_timeout(80)

    sliders.nth(0).evaluate("el => { el.value = '25'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    assert sliders.nth(0).input_value() == "25", (
        f"a plain decrease was itself clamped: {sliders.nth(0).input_value()!r}"
    )
    assert boxes.nth(0).input_value() == "25"
    assert page.locator("#improvement-total-value").inner_text() == "65.00%"
    #: The second row was never touched - not its value, not its max.
    assert boxes.nth(1).input_value() == "40"
    assert sliders.nth(1).get_attribute("max") == "100"


def test_no_destination_can_be_increased_once_the_allocation_reaches_one_hundred_percent(page_at):
    """**The one coupling item 2 keeps, on the control the client actually
    dragged.** Once every destination's shares sum to 100%, no single
    destination's SLIDER may be dragged any higher — `<input type="range">`
    cannot represent a value outside its own `max`, and `max` is this
    destination's own share plus whatever is left, which is nothing once the
    total is 100%. This is an upper bound only: below 100% the panel stays
    exactly as editable as it always was, in either direction, on both
    controls.

    **The number box is deliberately not asserted the same way.** It has
    always been allowed to hold a figure `improvementValidation` refuses —
    that is what disables Compare — and rewriting it here would be the same
    defect `test_the_improvement_percentage_refuses_a_minus_without_rewriting_the_number`
    exists to catch. So the box half of this test asserts the REFUSAL, not a
    clamp: Compare stays disabled and the box keeps exactly what was typed.

    **This test states the requirement; it is not what proves the fix.** With
    only two destinations at exactly 100%, `sliderMax` returns the same figure
    with or without its own floor (`headroom` is `0`, never negative, so
    rounding has nothing to floor), so a mutation there does not fail this
    specific test — confirmed by running it. The floor itself is what
    `test_dragging_one_destination_does_not_move_another` exercises (three or
    more destinations, one edit pushed past the headroom actually left), and
    the box/slider agreement under a coarse drag is what
    `test_a_coarse_drag_near_the_ceiling_does_not_leave_the_box_and_slider_disagreeing`
    exercises. This test is the plain, affirmative statement of the rule those
    two guard the mechanism of.
    """
    page = _improvement_panel(page_at)
    boxes = page.locator('.percentage-input input[type="number"]')
    sliders = page.locator('input[type="range"][data-improvement-code]')
    compare = page.locator('[data-action="compare-improvement"]')
    assert boxes.count() >= 2, "need two destinations to reach 100%"

    boxes.nth(0).fill("60")
    boxes.nth(1).fill("40")
    page.wait_for_timeout(80)
    assert page.locator("#improvement-total-value").inner_text() == "100.00%"
    assert compare.is_enabled()

    #: Dragging straight past the ceiling: the range cannot hold a value
    #: above its own `max`, so the browser refuses the assignment outright.
    sliders.nth(0).evaluate("el => { el.value = '100'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)
    assert float(sliders.nth(0).input_value()) <= 60.0001, (
        f"a destination's SLIDER was increased past the 100% ceiling by dragging: "
        f"{sliders.nth(0).input_value()!r}"
    )
    assert boxes.nth(0).input_value() == sliders.nth(0).input_value(), (
        "the box and the slider disagree after the drag was capped"
    )
    assert page.locator("#improvement-total-value").inner_text() == "100.00%"
    assert compare.is_enabled(), "capping the drag should not itself invalidate the allocation"

    #: Typing straight past the ceiling: the box keeps the figure verbatim,
    #: and `improvementValidation` is what refuses it.
    boxes.nth(0).fill("90")
    page.wait_for_timeout(80)
    assert boxes.nth(0).input_value() == "90", (
        "the number box rewrote a figure past the ceiling instead of the validation refusing it"
    )
    assert compare.is_disabled(), (
        "an allocation totalling well over 100% left Compare Impact enabled"
    )

    #: Below 100%, the panel stays editable in the ordinary direction, on both controls.
    boxes.nth(0).fill("50")
    page.wait_for_timeout(80)
    assert boxes.nth(0).input_value() == "50"
    assert page.locator("#improvement-total-value").inner_text() == "90.00%"
    assert compare.is_disabled(), "90% total should still fail the exactly-100 rule"


def test_a_coarse_drag_near_the_ceiling_does_not_leave_the_box_and_slider_disagreeing(page_at):
    """**The mechanism the test above cannot exercise on its own.** A drag is
    rounded to `rangeStep`'s coarseness (half a percentage point) before it is
    stored, and that rounding can land ABOVE a destination's own ceiling even
    though the raw pointer position was inside it - 0.29% rounds to 0.5% at a
    0.5-point step, which overshoots a ceiling of 0.3%. The browser silently
    clamps the range's own `.value` back to its `max` the instant that
    happens; without a matching clamp on the figure this module goes on to
    store and mirror, the sibling number box - and the allocation total - keep
    the OVERSHOT figure regardless, so the two controls for one destination
    read two different numbers and the total reads over 100%.

    Confirmed against the real defect first: before this fix, dragging to
    0.29% here left the range at `0.3` and the box at `0.5`, with the total
    reading `100.20%`.

    Mutation to confirm this test would catch the regression: in
    `updateImprovementInput` (`web/js/improvement.js`), disable the
    RANGE-only ceiling block (`if (control.type === 'range' && percentage !==
    '') { ... }`) and watch this fail.
    """
    page = _improvement_panel(page_at)
    boxes = page.locator('.percentage-input input[type="number"]')
    sliders = page.locator('input[type="range"][data-improvement-code]')

    #: 99.7% to the first destination leaves the second exactly 0.3% of
    #: headroom - inside one `rangeStep` (0.5) of overshooting on a drag.
    boxes.nth(0).fill("99.7")
    page.wait_for_timeout(80)
    #: The second slider's own `max` is fixed at 100 regardless (item ⑨ round
    #: two) - it is no longer where the 0.3% ceiling lives, so this drag has
    #: to be caught by `updateImprovementInput`'s own clamp, not by the
    #: browser refusing to assign a value above `max`.
    assert sliders.nth(1).get_attribute("max") == "100"

    sliders.nth(1).evaluate("el => { el.value = '0.29'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    assert sliders.nth(1).input_value() == "0.3", (
        f"the drag did not land on the expected step: {sliders.nth(1).input_value()!r}"
    )
    assert boxes.nth(1).input_value() == "0.3", (
        f"the box shows {boxes.nth(1).input_value()!r} while the slider shows "
        f"{sliders.nth(1).input_value()!r} for the same destination - the coarse drag "
        "overshot the ceiling and only the slider was clamped"
    )
    assert page.locator("#improvement-total-value").inner_text() == "100.00%", (
        "a coarse drag pushed the allocation's own total past 100%"
    )


def test_a_retreat_after_being_driven_down_does_not_survive_the_next_touch(page_at):
    """**The client's own failure sequence, not only the recomputation defect
    it starts from.** The client's report was reproduced by the code review at
    exactly this allocation: 20 kg, rows 5.90 / 7.80 / 1.70 / 4.55 kg (99.75%
    of the total), in the taxonomy's own sort order (prevention,
    refed_prevention, refed_donations, refed_animal_feed). `state.improvedAllocations`
    is a percentage in every mode (§7.3), so those four kilogram figures are
    entered here as the percentages of 20 kg they are - 29.50 / 39.00 / 8.50 /
    22.75, the same allocation, so the setup does not depend on switching the
    panel into unit mode first.

    It is three steps, not one, and the loss only appears on the third:

    1. Push Donations from 1.70 kg (8.50%) to 8.20 kg (41.00%) - 32.50 points
       past the 0.25% actually left. Every untouched destination's THUMB is
       driven down by the unfloored `sliderMax`, while the number boxes
       (never touched) stay put.
    2. Pull Donations back to 1.70 kg (8.50%) - the maxima recover, but on the
       unfixed code the driven-down THUMBS stay latched at wherever they were
       pushed to; the boxes still read what they always read.
    3. One ArrowDown then one ArrowUp on refed_prevention's OWN slider - net
       zero on a slider that never should have drifted. On the unfixed code
       this reads the LATCHED thumb position, not the figure the box beside
       it displays, and stores it: refed_prevention's box and
       `state.improvedAllocations` collapse from 39.00% (7.80 kg) towards the
       latched figure, and the total falls out of its 99.75% agreement. **This
       is the client's five-kilogram loss** - `test_dragging_one_destination_does_not_move_another`
       asserts the box before the slider and dies on the SLIDER assertion
       under the un-floored mutation, so the box never gets exercised and
       this exact sequence had no test.

    At HEAD, `sliderMax`'s floor means no thumb is ever driven below its own
    value in step 1, so there is nothing for step 2 to fail to restore and
    nothing for step 3 to latch onto - every assertion below holds unchanged.

    Mutation to confirm this test would catch the regression: restore
    `sliderMax`'s body in `web/js/improvement.js` to
    `Math.max(0, Math.round((typed(value) + headroom) * 100) / 100)` (drop
    the `numericValue` floor) and watch the BOX assertion below fail, not
    only a slider one.
    """
    page = _improvement_panel(page_at)
    boxes = page.locator('.percentage-input input[type="number"]')
    sliders = page.locator('input[type="range"][data-improvement-code]')
    assert boxes.count() >= 4, "need four destinations to set up the client's own allocation"

    #: prevention, refed_prevention, refed_donations, refed_animal_feed -
    #: 5.90 / 7.80 / 1.70 / 4.55 kg of 20, i.e. 29.50 / 39.00 / 8.50 / 22.75%,
    #: summing to 99.75%, the exact allocation the client held when this
    #: happened to them.
    for index, value in enumerate(["29.50", "39.00", "8.50", "22.75"]):
        boxes.nth(index).fill(value)
        page.wait_for_timeout(40)

    total_before = page.locator("#improvement-total-value").inner_text()
    assert total_before == "99.75%", f"the setup no longer matches the client's own allocation: {total_before!r}"

    #: Step 1: push Donations (index 2, 1.70 kg) to 8.20 kg, i.e. 8.50% to
    #: 41.00% - 32.50 points past the 0.25% actually left.
    boxes.nth(2).fill("41.00")
    page.wait_for_timeout(80)

    #: Step 2: pull Donations back to 1.70 kg, i.e. 8.50%.
    boxes.nth(2).fill("8.50")
    page.wait_for_timeout(80)

    box_before_touch = boxes.nth(1).input_value()
    assert box_before_touch == "39.00", (
        f"refed_prevention's own box already changed to {box_before_touch!r} before it was "
        "ever touched, on steps 1-2 alone"
    )

    #: Step 3: the client's own loss. ONE ArrowDown then ONE ArrowUp on
    #: refed_prevention's OWN slider - a round trip on a control nobody
    #: dragged and whose value never should have moved.
    sliders.nth(1).press("ArrowDown")
    page.wait_for_timeout(40)
    sliders.nth(1).press("ArrowUp")
    page.wait_for_timeout(80)

    #: ArrowDown/ArrowUp is a native round trip that may restate the figure
    #: without its trailing zeroes (percentage mode prints `improved` exactly
    #: as stored, with no forced two decimal places - see `DestinationAllocationRow`),
    #: so the box is compared numerically. What matters is that the FIGURE
    #: itself did not move, not its string formatting.
    box_after = boxes.nth(1).input_value()
    assert abs(float(box_after) - 39.00) < 1e-9, (
        f"refed_prevention's BOX collapsed to {box_after!r} after a single "
        "ArrowDown/ArrowUp round trip on its OWN slider - the client's own five-kilogram loss, "
        "read out of the number the visitor actually looks at rather than the thumb position"
    )
    total_after = page.locator("#improvement-total-value").inner_text()
    assert total_after == "99.75%", (
        f"the total moved to {total_after!r} even though every box reads what it read before "
        "step 3 - the allocation itself, not merely the slider, was lost"
    )


def test_a_box_typed_past_its_own_ceiling_does_not_leave_its_own_slider_stuck_below_it(page_at):
    """**A cosmetic desynchronisation, not the client's own report, but the same
    silent-clamp mechanism in one narrower case - and item ⑨ round two's fixed
    `max` closes it a different way than round one did.** A number box has
    always been allowed to hold a figure past its own row's ceiling - that is
    what disables Compare, and it is deliberate (see the docstring on
    `updateImprovementInput`). What is not deliberate is what its OWN slider
    ends up showing.

    Round one's fix was a `max` loop that ran before the mirror loop, so a
    slider's ceiling was raised to admit an out-of-range box value before that
    value was mirrored onto it. **That loop no longer exists, because there is
    nothing left for it to raise: every slider's `max` is now fixed at `100`
    (percentage mode) from the moment the row is drawn** (see `fixedRowMax`),
    which already exceeds any figure `improvementValidation`'s own 0-100 range
    check would let through Compare, so a box value mirrored onto its sibling
    slider is never rejected by a stale ceiling in the first place - there is
    no ordering left to get wrong.

    Mutation to confirm this test would still catch a regression of the
    underlying symptom: in `DestinationAllocationRow`, hard-code the range's
    own `max="50"` instead of `fixedRowMax(...)` and watch the slider
    assertion below fail once the box is typed past it.
    """
    page = _improvement_panel(page_at)
    boxes = page.locator('.percentage-input input[type="number"]')
    sliders = page.locator('input[type="range"][data-improvement-code]')
    assert boxes.count() >= 2, "need two destinations for one to hold all the headroom"

    boxes.nth(0).fill("50")
    boxes.nth(1).fill("50")
    page.wait_for_timeout(80)
    assert page.locator("#improvement-total-value").inner_text() == "100.00%"
    assert sliders.nth(1).get_attribute("max") == "100", (
        "the slider's own max should already be the fixed percentage ceiling, "
        "not this row's current share plus headroom"
    )

    #: Typed straight past this row's own ceiling (50, since headroom is
    #: already zero) - allowed on the box, refused nowhere until Compare.
    boxes.nth(1).fill("90")
    page.wait_for_timeout(80)

    assert boxes.nth(1).input_value() == "90", (
        "the box itself should hold exactly what was typed, unclamped"
    )
    assert sliders.nth(1).get_attribute("max") == "100", (
        f"the slider's own max should stay fixed, not follow the new figure: "
        f"{sliders.nth(1).get_attribute('max')!r}"
    )
    assert sliders.nth(1).input_value() == "90", (
        f"the slider reads {sliders.nth(1).input_value()!r} while its own box reads '90' - "
        "the fixed max should already have been wide enough to admit the mirrored value "
        "without any ceiling recompute at all"
    )


#: width, height, dpr - the same five breakpoints `page_at`'s own layout
#: tests use (320/390 stacked, 700/938/1278 not), each paired with a height
#: and dpr already established elsewhere in this file for that width.
_FIVE_WIDTHS = [
    pytest.param(320, 700, 3.0, id="320"),
    pytest.param(390, 700, 3.0, id="390"),
    pytest.param(700, 900, 1.0, id="700"),
    pytest.param(938, 898, 1.5, id="938"),
    pytest.param(1278, 983, 1.25, id="1278"),
]

#: The row and its controls stack below 560px - the width at
#: and above which the range/select/box comparison is meaningful at all.
_STACKING_BREAKPOINT = 560


@pytest.mark.parametrize("lang", ["de", "ar"])
@pytest.mark.parametrize("width,height,dpr", _FIVE_WIDTHS)
def test_the_range_gets_more_room_than_the_number_box_in_unit_mode(page_at_locale, width, height, dpr, lang):
    """**Item ⑨'s layout half, rewritten by #74 rather than retired.**

    This was `test_the_range_gets_more_room_than_the_select_in_unit_mode` and it
    measured three tracks: range, number box and the row's own unit `<select>`.
    The select is gone - the client asked on 17 September for one unit throughout
    the Improvement section, so the choice moved out of the row and up to
    `#improvement-mode` - and with it `.improvement-control-unit`, the three-track
    template this test was calibrated against.

    **What it was protecting, and what of that survives.** Half of it was the
    competition between the range and the select, and that half has no subject any
    more; the request it came from ("the slider the visitor drags should get the
    room") is now satisfied by construction, because the row has two controls
    instead of three and the range's track is `minmax(90px, 1fr)` against the
    box's fixed 108px. The other half is a **containment** check - the row's last,
    narrowest track must stay inside `.improvement-allocation-list`'s own box,
    which has `overflow: hidden` and so clips without raising a scrollbar - and
    that is still live and still worth five widths in two scripts. The number box
    inherits the role the select had: it is now the row's inline-end track, so it
    is what runs out of room first and what gets clipped first.

    Keeping the locale x width matrix rather than trimming it to English is the
    point of the test. The defect it was written against was found in German at
    700px and in Arabic off the opposite edge, and neither was visible in English.

    **Round three found this assertion checked only the left edge, in an
    LTR locale** (`box["x"] >= list_box["x"] - 1`) - which passes at
    700px even when 65% of the control is cut off the *right* edge, because
    nothing here ever looked at the right edge or ran in a locale where the
    left edge is the one that stays clean. Checking full containment - both
    edges, against the list's own box - catches a clip off either edge, in
    either direction.
    """
    page = advance_to(page_at_locale(width, height, dpr, lang), 5)
    page.click('[data-action="explore-improvements"]')
    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(120)

    row = page.locator(".improvement-allocation-row").first
    range_box = row.locator('input[type="range"]').bounding_box()
    box_box = row.locator('.percentage-input input[type="number"]').bounding_box()

    assert row.locator("select").count() == 0, (
        f"[{lang}@{width}px] a destination row still carries a `<select>` - #74 removed "
        "the per-row unit choice, so the only unit control on this panel is #improvement-mode"
    )

    if width >= _STACKING_BREAKPOINT:
        assert range_box["width"] >= box_box["width"], (
            f"[{lang}@{width}px] the range ({range_box['width']}px) is narrower than the "
            f"number box ({box_box['width']}px) - the control the visitor drags should get "
            "the room"
        )

    #: The panel must not silently clip past its own list container
    #: (`.improvement-allocation-list` has `overflow: hidden`) - a control
    #: whose box is not **fully contained** by the list's own box is being cut
    #: off rather than merely narrow. Checked on both edges: a left-edge-only
    #: check is exactly what let a 40px right-edge clip through in German at
    #: 700px, and would equally have missed a right-edge-only check catching
    #: Arabic's left-edge clip at the same width.
    list_box = page.locator(".improvement-allocation-list").bounding_box()
    assert box_box["x"] >= list_box["x"] - 1, (
        f"[{lang}@{width}px] the number box's own left/start edge ({box_box['x']}) sits "
        f"outside its list container's own left edge ({list_box['x']}) - it is being "
        "clipped, not merely narrow"
    )
    assert box_box["x"] + box_box["width"] <= list_box["x"] + list_box["width"] + 1, (
        f"[{lang}@{width}px] the number box's own right/end edge "
        f"({box_box['x'] + box_box['width']}) sits outside its list container's own "
        f"right edge ({list_box['x'] + list_box['width']}) - it is being clipped, not "
        "merely narrow"
    )


@pytest.mark.parametrize("lang", ["de", "ar"])
@pytest.mark.parametrize("width,height,dpr", _FIVE_WIDTHS)
def test_a_step_heading_does_not_overflow_in_translation(page_at_locale, width, height, dpr, lang):
    """Every step's `<h1>` shares one rule (`h1` in ``styles.css``), and until
    now that rule had no ``overflow-wrap`` at all — unlike every other
    long-content-risk element on this site (``.destination-group__name``,
    ``.review-destinations dd``, ``.home-page .home-hero h1``), which already
    carry ``overflow-wrap: anywhere``.

    **German at 320px is where this actually broke**, on the food-category
    step: "Welche Art von Lebensmittelabfall erfassen Sie?" puts
    ``Lebensmittelabfall`` alone past the 280px column
    ``.main-content``'s own padding leaves at that width, and with nowhere to
    break, the *word* — not the `<h1>`'s own box, which never moved — carried
    22px past its right edge. 20px of that lands back inside
    ``.main-content``'s own right padding; the last 2px is exactly the
    ``documentElement.scrollWidth`` overflow this file's own
    ``test_no_horizontal_overflow_at_any_breakpoint`` never caught, because
    that test runs English only, at three widths, and the word that does not
    fit is German, at a fourth and fifth this file otherwise only exercises
    through ``test_the_range_gets_more_room_than_the_select_in_unit_mode``.

    This reuses that test's own ``_FIVE_WIDTHS`` and ``page_at_locale``
    rather than adding a parallel width/language mechanism, and checks the
    food-category step specifically — the step this defect was actually
    found on — rather than asserting only in general.

    Mutation: dropping ``overflow-wrap: anywhere`` from the `h1` rule
    reproduces the exact 2px overflow this test is pinned against, and it
    fails.
    """
    page = page_at_locale(width, height, dpr, lang)
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')

    measured = page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
    )
    assert measured["scroll"] <= measured["client"], (
        f"[{lang}@{width}px] the food-category step overflows by "
        f"{measured['scroll'] - measured['client']}px"
    )


def test_the_sliders_start_at_zero_and_the_total_says_so(page_at):
    """The client asked for "所有滑块默认都是 0".

    The panel currently seeds each destination with its CURRENT share, which
    is a reasonable starting point and is not what was asked for: a visitor
    modelling an improvement is choosing a new allocation, and starting from
    the old one hides which numbers they have actually decided.
    """
    page = _improvement_panel(page_at)

    values = page.locator('input[type="range"][data-improvement-code]').evaluate_all(
        "els => els.map(el => el.value)"
    )
    assert set(values) == {"0"}, f"sliders did not start at zero: {values}"
    assert "0.00" in page.locator("#improvement-total-value").inner_text()


def test_the_compare_button_is_still_gated_on_exactly_one_hundred(page_at):
    """**The affirmative half, and the rule this task must not break.**

    A slider that cannot overshoot could be built by clamping the total to
    100 and enabling the button - which would let 99.99% through and quietly
    change what the alternative scenario means. The gate stays.
    """
    page = _improvement_panel(page_at)
    sliders = page.locator('input[type="range"][data-improvement-code]')

    sliders.nth(0).evaluate("el => { el.value = '60'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    assert page.locator('[data-action="compare-improvement"]').is_disabled(), (
        "Compare is enabled at 60% - the exactly-100 rule has been weakened"
    )

    sliders.nth(1).evaluate("el => { el.value = '40'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    assert page.locator('[data-action="compare-improvement"]').is_enabled()


def test_the_improvement_panel_can_be_driven_in_unit_mode(page_at):
    """Item ⑧. One control, and kilograms are the quantity the panel already
    works in underneath: `improvedLines` computes
    `totalKg * percentage / 100` before it sends anything.

    So this is a display and entry mode, not a second calculation - which is
    also why the control cannot change what is sent.

    **#74 collapsed two controls into this one.** Between 2026-09-05 and
    17 September the panel had a percentage/unit toggle *and* a `<select>` on every
    destination row; the client then asked for one consistent unit throughout the
    section, so `#improvement-mode` now offers the units themselves - percentage,
    the two weights, and every container the taxonomy carries - and there is no
    second control anywhere on the panel. Both halves are asserted: the options are
    on `#improvement-mode`, and no row has a `<select>` at all.
    """
    page = _improvement_panel(page_at)

    control = page.locator("#improvement-mode")
    assert control.count() == 1, "no unit control"

    values = control.locator("option").evaluate_all("els => els.map(el => el.value)")
    assert values[:3] == ["percentage", "kilograms", "tonnes"], (
        f"the one unit control does not open with percentage and the two weights: {values!r}"
    )
    presets = [value for value in values if value.startswith("preset:")]
    assert presets, (
        f"the one unit control offers no container at all: {values!r} - #74 asks for every "
        "unit the taxonomy carries on this control, not only the weights"
    )

    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(80)

    assert page.locator("#improvement-mode").input_value() == "kilograms"
    assert page.locator(".improvement-allocation-row select").count() == 0, (
        "a destination row still carries a unit `<select>` of its own - #74 removed the "
        "per-row choice, and a second control is what it asked to be rid of"
    )
    #: The `%` suffix is the one thing that stays per row, and only where the
    #: figure already is the unit (`improvement.js`'s `unitSuffix`).
    suffixes = page.locator(".percentage-input span").all_inner_texts()
    assert suffixes == [], (
        f"a row still prints a unit suffix in unit mode: {suffixes!r} - a container label is "
        "254px of staff text and the suffix track is 100px, which is the clip the client "
        "complained about"
    )


def test_the_one_unit_control_names_its_own_selection_in_full(page_at):
    """A container's label is staff text printed verbatim (§7.7.7) and can be wider
    than the closed control at a phone width, so the full name has to be reachable
    from somewhere other than the pixels.

    `title` is that somewhere, and it is the same reasoning the per-row select
    carried before #74 moved the choice up here: `text-overflow: ellipsis` is not
    reliable on a closed `<select>` across browsers, so there is no in-band signal
    that a name was cut, and the native dropdown - which does draw each option at
    its own width - is only open while the visitor holds it open.

    Asserted against the *selected* option's own text rather than a hard-coded
    string, so this measures whatever the published taxonomy named the container.
    """
    page = _improvement_panel(page_at)
    options = page.locator("#improvement-mode option").evaluate_all(
        "els => els.map(el => ({value: el.value, label: el.textContent}))"
    )
    containers = [option for option in options if option["value"].startswith("preset:")]
    assert containers, "no container option to select"
    #: The longest one, because it is the one whose name is actually at risk.
    widest = max(containers, key=lambda option: len(option["label"]))

    page.select_option("#improvement-mode", widest["value"])
    page.wait_for_timeout(120)

    fit = page.evaluate(MODE_SELECT_FIT)
    assert fit["selectedLabel"] == widest["label"], fit
    assert fit["title"] == widest["label"], (
        f"the unit control's title is {fit['title']!r} where its selected option reads "
        f"{widest['label']!r} - a closed select that cannot show the whole name and does "
        "not carry it on a tooltip either leaves the visitor guessing from what fits"
    )


def test_the_accessible_names_name_the_panel_s_own_unit(page_at):
    """The follow-up the coordinator raised on fix round 1.

    An `aria-label` is the only message a screen-reader visitor gets for a
    control - there is no visible text to fall back on. The range and the
    number box both carried `aria-label="Improved <destination> percentage"`
    / `"... percentage value"` unconditionally, so a visitor working in unit
    mode was told, on the one channel they could hear it, that the field
    wanted a percentage. Same defect as the validation message fixed
    alongside it, one layer further from what a sighted visitor notices.

    **Since #74 this is the only per-control statement of the unit on the panel**,
    which makes it load-bearing rather than a nicety. The row's visible suffix is
    gone - a container's staff-typed label does not fit a row-sized track, which is
    the clip the client complained about - so the unit is stated once, visibly, by
    the control above the cards, and once per control here, where there is no
    control above to glance at.
    """
    page = _improvement_panel(page_at)

    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(80)

    range_label = page.locator('input[type="range"][data-improvement-code]').nth(0).get_attribute("aria-label")
    box_label = page.locator('.percentage-input input[type="number"]').nth(0).get_attribute("aria-label")

    for label, name in ((range_label, "range"), (box_label, "number box")):
        assert label is not None, f"the {name} lost its accessible name entirely"
        assert "percentage" not in label.lower(), (
            f"the {name}'s accessible name still says percentage in unit mode: {label!r}"
        )
        assert "kilogram" in label.lower(), (
            f"the {name}'s accessible name does not name the panel's own unit (kilograms): "
            f"{label!r}"
        )


def test_switching_mode_preserves_the_allocation(page_at):
    """**The assertion that makes this a view and not a reset.**

    A visitor who has allocated 60/40 and switches to unit mode must see the
    same allocation expressed differently - not two empty boxes. Rebuilding
    the panel on toggle is the obvious implementation and it silently throws
    away their work.
    """
    page = _improvement_panel(page_at)
    sliders = page.locator('input[type="range"][data-improvement-code]')

    sliders.nth(0).evaluate("el => { el.value = '60'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    sliders.nth(1).evaluate("el => { el.value = '40'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    total_kg = float(page.locator("#improvement-total-kg").inner_text().replace(",", ""))

    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(80)

    boxes = page.locator('.percentage-input input[type="number"]').evaluate_all(
        "els => els.map(el => Number(el.value))"
    )
    assert abs(boxes[0] - total_kg * 0.6) < 0.01, (
        f"60% did not become 60% of the mass: {boxes[0]} against {total_kg}"
    )


def test_the_request_is_unchanged_by_the_mode(page_at):
    """The unit is a way of typing, and the wire never learns which was used.

    Asserted by driving the same allocation once per unit and comparing the
    bodies: a unit that changed what is sent would be a second calculation path,
    and they would drift.

    **Every unit the one control offers, not two of them** (#74). Percentage and
    kilograms were the two the toggle had; `tonnes` is the unit whose conversion
    divides by a thousand, and a container is the only one that multiplies by a
    figure read out of the taxonomy (`kg_per_unit`) rather than by a constant - so
    it is the one a stored display value would show up in first, and it is the unit
    this panel never offered panel-wide before.
    """
    page = _improvement_panel(page_at)
    bodies = []
    page.route(
        "**/api/v1/calculate",
        lambda route: (bodies.append(route.request.post_data_json), route.abort()),
    )

    sliders = page.locator('input[type="range"][data-improvement-code]')
    sliders.nth(0).evaluate("el => { el.value = '60'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    sliders.nth(1).evaluate("el => { el.value = '40'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)
    page.click('[data-action="compare-improvement"]')
    page.wait_for_timeout(400)

    container = page.locator("#improvement-mode option").evaluate_all(
        "els => els.map(el => el.value).filter(value => value.startsWith('preset:'))"
    )
    assert container, "the one unit control offers no container to drive"
    for unit in ("kilograms", "tonnes", container[0]):
        page.select_option("#improvement-mode", unit)
        page.wait_for_timeout(120)
        page.click('[data-action="compare-improvement"]')
        page.wait_for_timeout(400)

    assert len(bodies) == 4, bodies
    for index, body in enumerate(bodies[1:], start=1):
        assert body["entries"][0]["alternative"] == bodies[0]["entries"][0]["alternative"], (
            f"request {index} differs from the percentage one: the display unit reached the "
            f"wire. {body['entries'][0]['alternative']!r} against "
            f"{bodies[0]['entries'][0]['alternative']!r}"
        )


def test_a_kilogram_split_that_loses_a_digit_still_totals_exactly_100(page_at):
    """**The case the 60/40 split above cannot exercise.**

    60% and 40% of the 1,000 kg fixture entry are `600.000` and `400.000` -
    already exact at two decimal places, so a percentage that is rounded
    before it is stored survives that split by luck rather than by
    correctness. This uses four destinations whose kilogram figures were
    chosen so each one's *own* percentage share lands past the second decimal
    place - `10.005`, `20.015`, `30.025`, `39.955` of a 1,000 kg total - while
    the four kilogram figures themselves (`100.05 + 200.15 + 300.25 +
    399.55`) still sum to exactly `1000.00`.

    A conversion that rounds each destination's percentage to two places
    *before* storing it - rather than keeping the exact value and rounding
    only where it is displayed - drifts the total by two hundredths of a
    percentage point in the same direction on every one of the four, which
    is comfortably past `improvementValidation`'s own 0.01 tolerance.

    The one unit control is set to kilograms, so this exercises the kilogram path
    exactly as it did before the panel's unit was a per-row choice (2026-09-05) and
    after it stopped being one (#74).
    """
    page = _improvement_panel(page_at)
    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(80)

    boxes = page.locator('.percentage-input input[type="number"]')
    assert boxes.count() >= 4, "need four destinations to test this split"
    for index, kilograms in enumerate(["100.05", "200.15", "300.25", "399.55"]):
        boxes.nth(index).fill(kilograms)
        page.wait_for_timeout(40)

    total = page.locator("#improvement-total-value").inner_text().strip()
    assert total == "100.00%", (
        f"a kilogram split that sums exactly to the entry's mass read back as {total!r}"
    )
    classes = page.locator(".improvement-total").get_attribute("class")
    assert "invalid" not in classes, f"a valid allocation was flagged invalid: {classes!r}"
    assert page.locator('[data-action="compare-improvement"]').is_enabled()

    # **The seam with the unit/percentage toggle itself.** `sliderMax` returns a
    # PERCENTAGE ceiling (`maxPercent`, at most 100 on any entry); `updateImprovementInput`
    # has to convert that through `displayAmount` before it lands on a unit-mode slider's
    # `max` attribute, or every such slider is capped at a number sized for percentage
    # points - `100` kg on this 1,000 kg entry - long before its real headroom. The first
    # destination's own share here is ~100.05 kg, comfortably past that percentage-sized
    # ceiling, so a `max` at or below 100 proves the conversion was skipped.
    first_max = float(page.locator('input[type="range"][data-improvement-code]').nth(0).get_attribute("max"))
    assert first_max > 100, (
        f"the unit-mode slider's ceiling is percentage-sized ({first_max!r}); "
        "updateImprovementInput must read it back through displayAmount"
    )


def test_changing_the_unit_converts_the_figure_rather_than_reinterpreting_it(page_at):
    """**The one rule item 1 must not get wrong, and the one property #74 could
    not be allowed to lose.** Switching the panel from kilograms to tonnes must
    restate the same mass, not multiply it by a thousand: a row holding 5.90 kg
    becomes 0.00590 t, never `5.90` t.

    This was `test_changing_one_row_s_unit_...` and drove a `<select>` inside the
    row; #74 moved the choice to `#improvement-mode` and the property is unchanged
    by that, because the conversion was never per row - `kgToUnitAmount` /
    `unitAmountToKg` do it and read a unit, not a destination.

    **Its own history is a defect of exactly this kind.** `lineKg` once seeded the
    sliders through `massToKg(qtyInput, entry.totalUnit)`, which reinterpreted a
    tonnes row as kilograms and applied no container preset at all, so two rows of
    equal mass seeded at 99.88% and 0.12% (`web/js/improvement.js`'s note on
    `lineKg`).

    Mutation to confirm this test would catch a reinterpretation: change
    `kgToUnitAmount`'s `unit === 'tonnes'` branch in `web/js/units.js` from
    `kg / 1000` to `kg` (a no-op "conversion") and watch this fail with the
    box reading `5.90` instead of `0.00590`.
    """
    page = _improvement_panel(page_at)
    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(80)

    box = page.locator('.percentage-input input[type="number"]').nth(0)
    box.fill("5.90")
    page.wait_for_timeout(60)

    page.select_option("#improvement-mode", "tonnes")
    page.wait_for_timeout(120)

    converted = float(page.locator('.percentage-input input[type="number"]').nth(0).input_value())
    assert abs(converted - 0.0059) < 0.0001, (
        f"5.90 kg switched to tonnes should read about 0.0059, not {converted!r} - "
        "a value near 5.90 would mean the figure was reinterpreted rather than converted"
    )


def test_changing_the_unit_changes_every_row_s_figure_and_no_row_s_allocation(page_at):
    """**The reversal.** This test is the opposite of the one it replaces, and
    deliberately keeps its shape so the diff reads as a reversal and not a deletion.

    What stood here was `test_changing_one_row_s_unit_does_not_change_another_row_s`:
    *"The unit selector is per row - `data-improvement-unit-code` on each `<select>`,
    read by its own `code` in the `change` handler - so switching one destination's
    display unit must leave every other destination's own unit, and its own figure,
    exactly where they were."* That was a real client ask, made on 2026-09-05, and it
    was correct until 17 September, when the client asked for the opposite: one
    consistent unit throughout the Improvement section (#74). **Both asks are real and
    the later one wins**, so the invariant is now its own inverse - one control, every
    row.

    Two assertions, because "every row moved" and "no allocation moved" are the two
    halves that make this a display change:

    * every row's *displayed* figure is restated in the new unit, including rows the
      visitor never touched. A per-row unit surviving anywhere - a stray
      `improvementRowUnits` lookup, a row drawn from a different key - leaves one row
      reading kilograms while the control says tonnes, which is #74 arriving again.
    * `state.improvedAllocations` does not move at all. There is no global holding the
      state, so it is read through the two things that are derived from it and from
      nothing else: the running total (`allocationTotal`, summed over the stored
      percentages) and the donut's own slice titles, which `PieChart` writes as
      `"<destination> - <share>%"` straight off the stored figure. A unit that reached
      the state would show in both at once. `test_the_request_is_unchanged_by_the_mode`
      closes the same question from the wire's end.
    """
    page = _improvement_panel(page_at)
    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(80)

    boxes = page.locator('.percentage-input input[type="number"]')
    assert boxes.count() >= 2, "need two destinations to test this"

    #: 60/40 rather than one row, so the "every row" half has an untouched row with a
    #: non-zero figure in it - a zero converts to a zero in any unit and would prove
    #: nothing.
    sliders = page.locator('input[type="range"][data-improvement-code]')
    sliders.nth(0).evaluate("el => { el.value = '60'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    sliders.nth(1).evaluate("el => { el.value = '40'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    before = boxes.evaluate_all("els => els.map(el => el.value)")
    total_before = page.locator("#improvement-total-value").inner_text().strip()
    shares_before = page.locator(".improvement-pie-chart path title").all_inner_texts()

    page.select_option("#improvement-mode", "tonnes")
    page.wait_for_timeout(150)

    after = page.locator('.percentage-input input[type="number"]').evaluate_all(
        "els => els.map(el => el.value)"
    )
    assert len(after) == len(before)
    #: Every row that held a figure now holds it a thousand times smaller, and that is
    #: asserted row by row rather than on the first one: one row left behind in
    #: kilograms is exactly the defect.
    moved = 0
    for index, (was, now) in enumerate(zip(before, after)):
        if float(was or 0) == 0:
            continue
        moved += 1
        assert abs(float(now) - float(was) / 1000) < 1e-6, (
            f"row {index} read {was!r} in kilograms and {now!r} in tonnes - one control "
            "governs every row since #74, so every row with a figure in it had to be "
            f"restated (all rows before: {before!r}, after: {after!r})"
        )
    assert moved >= 2, (
        f"only {moved} row(s) held a figure, so 'every row moved' was not actually tested: "
        f"{before!r}"
    )

    assert page.locator("#improvement-total-value").inner_text().strip() == total_before, (
        "the running total moved when only the display unit changed - it is computed from "
        "state.improvedAllocations, so this says a unit reached the stored allocation"
    )
    assert page.locator(".improvement-pie-chart path title").all_inner_texts() == shares_before, (
        "the donut's slice shares moved when only the display unit changed; "
        "state.improvedAllocations holds percentages in every unit (§7.3a) and the chart "
        f"reads it directly (before: {shares_before!r})"
    )


#: Item 11: the client's report was "the gap between cards differs between
#: steps 1 and 2" - the sector step and the food-type step, the only two
#: screens shaped alike enough to compare side by side (a `<p class="section-intro">`,
#: then one fieldset, then the step bar, with nothing else on the screen). Measuring
#: `.content-section` itself found it identical on every step already - 0 margin, 0
#: padding, every screen - so the divergence the client saw was never in the section
#: the two `.content-section wide` steps share with the four plain ones; it was in
#: the first thing inside it. `.stage-fieldset` opened with `margin: 34px 0 0`;
#: `.choice-fieldset` opened with `margin: 30px 0 18px` - the same role, 4px apart,
#: on the one pair of steps where a visitor can see both in a row.
CONTENT_SECTION_BOX = """
() => {
  const section = document.querySelector('.content-section');
  if (!section) return null;
  const cs = getComputedStyle(section);
  return {
    marginBlockStart: cs.marginBlockStart,
    marginBlockEnd: cs.marginBlockEnd,
    paddingBlockStart: cs.paddingBlockStart,
    paddingBlockEnd: cs.paddingBlockEnd,
    rowGap: cs.rowGap,
  };
}
"""

FIRST_PANEL_MARGIN_BLOCK_START = """
(selector) => {
  const el = document.querySelector(selector);
  return el ? getComputedStyle(el).marginBlockStart : null;
}
"""


@pytest.mark.parametrize("width,height,dpr", [VIEWPORTS[0], VIEWPORTS[2]])
def test_content_section_outer_spacing_is_equal_on_every_step(page_at, width, height, dpr):
    """`.content-section`'s own margin-block, padding-block and row-gap, walked across
    all seven screens at 1278 and 390 - the two widths the item 11 measurement pass
    used - and asserted identical.

    This already reads the same box on every screen (0 margin, 0 padding, `normal`
    gap): the outer container was never the divergence. It is asserted here anyway,
    so a future change that gives one step its own `.content-section` padding - the
    obvious place to reach for a "quick" per-step spacing fix - fails a test instead
    of surfacing in the next demonstration.
    """
    page = page_at(width, height, dpr)
    boxes = {}
    for step in walk(page):
        page.wait_for_timeout(60)
        box = page.evaluate(CONTENT_SECTION_BOX)
        if box is not None:
            boxes[step] = box
    assert len(boxes) >= 5, f"too few steps rendered a .content-section to compare: {boxes}"
    first_step, first_box = next(iter(boxes.items()))
    mismatched = {step: box for step, box in boxes.items() if box != first_box}
    assert not mismatched, (
        f".content-section's own spacing is not equal across steps: step {first_step} "
        f"measured {first_box}, but {mismatched} differ from it"
    )


@pytest.mark.parametrize("width,height,dpr", [VIEWPORTS[0], VIEWPORTS[2]])
def test_the_gap_above_the_first_panel_matches_between_the_sector_and_food_type_steps(page_at, width, height, dpr):
    """The actual item 11 defect, measured directly: the sector step's `.stage-fieldset`
    and the food-type step's `.choice-fieldset` sit in the identical position - directly
    after the intro paragraph, directly before the step bar - and are the only two panels
    in the wizard alike enough for a visitor to notice one sitting closer than the other.

    Before the fix this failed at both viewports with `34px` against `30px`. The fix
    changed `.choice-fieldset`'s `margin-top` to match `.stage-fieldset`'s rather than
    giving either one a new override; `.choice-fieldset`'s bottom margin is untouched,
    because it alone clears an optional "Clear optional selection" button `.stage-fieldset`
    never renders.
    """
    page = page_at(width, height, dpr)
    margins = {}
    for step in walk(page):
        if step == 0:
            margins[0] = page.evaluate(FIRST_PANEL_MARGIN_BLOCK_START, ".stage-fieldset")
        elif step == 1:
            margins[1] = page.evaluate(FIRST_PANEL_MARGIN_BLOCK_START, ".choice-fieldset")
            break
    assert margins.get(0) and margins.get(1), f"could not measure both panels: {margins}"
    assert margins[0] == margins[1], (
        f"the gap above the first panel differs between the sector step ({margins[0]}) "
        f"and the food-type step ({margins[1]})"
    )
