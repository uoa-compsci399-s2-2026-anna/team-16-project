"""Step 3's two zones, and the term tooltips that sit on their labels.

**What this file defends, and why it is geometry rather than markup.** The
client's report was that step 3's two columns had no visual separator: reading
across drifted out of one column into the next, and the reader only noticed when
a sentence failed to line up. Two defects were found while measuring it. The
first is the one that makes markup assertions worthless here — ``.amount-grid
--leaf`` was a class with **no rule anywhere in the stylesheet**, so a leaf card
inherited ``.amount-grid``'s two-column template while holding one child and left
445px of a 960px card dead, with the three live controls crushed into
130/150/130px beside it. Every class name involved was present and correct the
whole time. A test that had asserted the class list would have been green
throughout, which is why nothing below reads a class to decide whether a rule is
in force: it measures the box.

The second defect was copy — the two NZ$ fields carried the *same* hint
sentence, verbatim — and ``test_the_two_money_fields_do_not_repeat_one_hint``
below is what keeps it from coming back.

**The tooltip's three ways in are three separate tests on purpose.** A single
test covering hover, keyboard and tap would report "the tooltip did not open"
and leave the reader to work out which input it was reached by, and the three
are held up by three different mechanisms — ``:hover``, ``:focus-within``, and a
line of JavaScript in the delegated click handler. The tap one is the one that
had to be got right, and its own docstring records why the obvious version of it
is a false green.

**Everything here is measured against the running stack**, and the stack serves
a *baked* copy of ``web/`` (``docker/web.Dockerfile`` does ``COPY web/``, not a
bind mount), so an edit to the front end means::

    docker compose -f docker/compose.yaml build web
    docker compose -f docker/compose.yaml up -d web

before any of this measures anything. Skipping that measures the previous image.

Requires Playwright and the stack::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d
    pytest tests/web/test_step_three_zones_browser.py
"""

from __future__ import annotations


import pytest

from tests.web.base_url import CALCULATOR
from tests.web.steps import expand_step_cards, press_continue


pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure a rendered layout; the zones and the tooltip are unverified without it",
)

BASE = CALCULATOR

#: The breakpoint `.zones` splits at, shared with the rest of step 3's layout.
BREAKPOINT = 650

#: `body { min-width: 320px }` is a deliberate floor - below 320px of content the
#: interface stops reflowing and lets the reader scroll. Same constant and same
#: reasoning as `test_leaf_layout_browser.py` and `test_horizontal_overflow.py`.
MIN_CONTENT_WIDTH = 320

#: `html { scroll-behavior: smooth }` otherwise returns mid-animation geometry
#: from a `getBoundingClientRect` taken straight after a focus - the same
#: instrumentation `test_step_navigation.py` injects, and for the same reason.
FORCE_AUTO = "html { scroll-behavior: auto !important; }"

#: Everything the tooltip tests need about one panel, in one round trip: whether
#: it is being shown, where it is, and who holds focus while it is.
TIP_STATE = """
(selector) => {
  const tip = document.querySelector(selector);
  if (!tip) return null;
  const style = getComputedStyle(tip);
  const box = tip.getBoundingClientRect();
  const active = document.activeElement;
  return {
    visibility: style.visibility,
    opacity: style.opacity,
    left: Math.round(box.left),
    right: Math.round(box.right),
    width: Math.round(box.width),
    activeIsTheTerm: !!(active && active.classList && active.classList.contains('term')),
    activeDescription: active ? (active.id || active.className || active.tagName) : null,
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    innerWidth: window.innerWidth,
  };
}
"""

#: One panel, reduced to the numbers every layout test below asks about. The
#: zone's own `.form-field`s only - `querySelectorAll` from the zone would be the
#: same set today, but scoping it says what is meant.
PANELS = """
() => {
  const round = value => Math.round(value);
  const boxOf = el => {
    const b = el.getBoundingClientRect();
    return {x: round(b.x), right: round(b.right), width: round(b.width), height: round(b.height), top: round(b.top)};
  };
  return [...document.querySelectorAll('.leaf-panel-list .form-panel')].map(panel => {
    // **The content box is the element that holds the padding, which is not always
    // the panel.** Since #134 a leaf card is a collapsible shell: the panel's own
    // padding is zero and the header button and `.step-card__body` carry it, so a
    // content box derived from the panel would sit 20px outside the fields and
    // `test_a_leaf_cards_fields_span_the_card` would fail on a layout that is
    // correct. The single-leaf panel is not a card and still holds its own padding.
    const content = panel.querySelector('.step-card__body') || panel;
    const style = getComputedStyle(content);
    const contentBox = boxOf(content);
    const box = boxOf(panel);
    return {
      tag: panel.tagName,
      legend: panel.querySelector('legend') ? panel.querySelector('legend').textContent.trim() : null,
      box,
      contentStart: contentBox.x + parseFloat(style.paddingInlineStart) + parseFloat(style.borderInlineStartWidth),
      contentEnd: contentBox.right - parseFloat(style.paddingInlineEnd) - parseFloat(style.borderInlineEndWidth),
      disclosures: panel.querySelectorAll('details').length,
      zones: [...panel.querySelectorAll('.zone')].map(zone => ({
        heading: zone.querySelector('h2') ? zone.querySelector('h2').textContent.trim() : null,
        sub: zone.querySelector('.zone-sub') ? zone.querySelector('.zone-sub').textContent.trim() : null,
        shown: typeof zone.checkVisibility === 'function' ? zone.checkVisibility() : true,
        box: boxOf(zone),
        fields: [...zone.querySelectorAll(':scope > .form-field')].map(field => {
          const control = field.querySelector('input, select');
          return {
            id: control ? control.id : null,
            box: boxOf(field),
            controlWidth: control ? round(control.getBoundingClientRect().width) : null,
            hint: field.querySelector('.field-hint') ? field.querySelector('.field-hint').textContent.trim() : null,
          };
        }),
      })),
    };
  });
}
"""


@pytest.fixture
def step_three(browser):
    """A page standing on step 3, at a given viewport and leaf count.

    A local fixture rather than `test_step_navigation.py`'s `page_at`, because
    every case here needs one of two things that file's fixture does not offer:
    a **forked chain** (its `walk` deliberately unticks every category again, so
    `advance_to(page, 2)` always lands on the single category-less leaf) and, for
    the tap case, a context with `has_touch`.

    `bypass_csp` is instrumentation, exactly as it is there: `style-src 'self'`
    correctly refuses the `<style>` element `add_style_tag` creates, and
    `FORCE_AUTO` is the only thing being injected. The policy itself is enforced
    against a real browser with no bypass in `tests/web/test_csp.py`.
    """
    contexts = []

    def open_at(width, height=700, leaves=1, has_touch=False):
        context = browser.new_context(
            viewport={"width": width, "height": height},
            locale="en-NZ",
            bypass_csp=True,
            has_touch=has_touch,
        )
        contexts.append(context)
        page = context.new_page()
        try:
            page.goto(BASE + "?lang=en", wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {BASE}: {error}")
        page.add_style_tag(content=FORCE_AUTO)
        page.wait_for_selector('[data-action="start"]', timeout=10000)

        page.click('[data-action="start"]')
        page.wait_for_selector('input[name="sector"]')
        page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
        page.wait_for_timeout(80)
        press_continue(page)
        page.wait_for_selector('input[name="food-category"]')
        if leaves > 1:
            #: One chosen category is one leaf. Step 2.5 is walked straight
            #: through without ticking a food, which `steps.py` records as
            #: changing nothing: a category with no food ticked yields exactly
            #: the leaf it yielded before that panel existed.
            boxes = page.locator('input[name="food-category"]')
            offered = boxes.count()
            assert offered > leaves, f"step 2 offers {offered} choices, too few to tick {leaves}"
            for index in range(leaves):
                boxes.nth(index).click()
                page.wait_for_timeout(50)
        press_continue(page)
        #: `state="attached"`, then open the cards. Since #134 a forked chain
        #: draws collapsed cards and a collapsed card's body is `hidden`, so the
        #: fields are in the document and not visible -- which the default
        #: `state="visible"` waits out against a correct screen. Everything below
        #: measures a card's contents, so every card is opened first, through the
        #: control a visitor would press.
        page.wait_for_selector('[data-leaf-field="amount"]', state="attached", timeout=10000)
        expand_step_cards(page)
        rendered = page.locator('[data-leaf-field="amount"]').count()
        assert rendered == leaves, (
            f"step 3 rendered {rendered} waste-amount fields when {leaves} leaves were asked for"
        )
        return page

    yield open_at
    for context in contexts:
        context.close()


def _term(page, control_id):
    """The `.term` on the label of the field whose control carries `control_id`."""
    return page.locator(f'label[for="{control_id}"] .term')


def _tip_selector(control_id):
    return f'label[for="{control_id}"] .tip'


# --------------------------------------------------------------------- the zones


@pytest.mark.parametrize("leaves", (1, 3))
def test_both_zones_are_on_screen_and_nothing_is_behind_a_disclosure(step_three, leaves):
    """Every step-3 panel carries both zones, both drawn, and no `<details>`.

    **The money pair being *visible* is the change, not the money pair existing.**
    A leaf card used to put the production total and the two NZ$ figures behind a
    `<details class="leaf-extras">`, on the stated ground that §7.6.3 forbids
    advancing from ever requiring scrolling and three full panels could not hold
    that at 390x700. That reason was written while the step bar was static; the
    bar is `position: sticky` now, so panel height no longer decides whether
    Continue is reachable, and the disclosure was removed rather than left
    hiding two of the five questions behind a summary nobody opens.

    Asserted on a three-leaf chain as well as on the single leaf, because the
    disclosure only ever existed on the multi-leaf branch - the single-leaf
    screen would have been green through the entire period the defect shipped.

    **#134 folds the whole card and this still holds, because the two claims are
    different.** What is refused here is a question *inside* a panel being
    reachable only by opening something; what #134 added is a fold around the
    *whole* card, named by the food, with a badge on the outside saying whether
    that card is finished. The fixture opens every card before measuring, so what
    is asserted is still "nothing inside an open card is hidden" - and the card's
    own chrome is a `<button>` rather than a `<details>`, so the count below
    cannot be satisfied by the fold.

    Mutation: putting the two NZ$ fields back inside a
    `<details class="leaf-extras"><summary>` in `leafPanel` fails at the
    disclosure count, naming the panel.
    """
    page = step_three(1278, 983, leaves=leaves)
    panels = page.evaluate(PANELS)

    assert len(panels) == leaves, (
        f"step 3 drew {len(panels)} panels for {leaves} leaves: {[p['legend'] for p in panels]}"
    )
    for index, panel in enumerate(panels):
        name = panel["legend"] or "the single leaf"
        assert panel["disclosures"] == 0, (
            f"panel {index} ({name}) still hides part of step 3 behind "
            f"{panel['disclosures']} <details> disclosure(s); every question on this "
            f"step is meant to be on screen without being opened first"
        )
        zones = panel["zones"]
        assert len(zones) == 2, (
            f"panel {index} ({name}) has {len(zones)} zones rather than two: "
            f"{[zone['heading'] for zone in zones]}"
        )
        for zone in zones:
            assert zone["heading"], f"a zone on panel {index} ({name}) carries no heading: {zone}"
            assert zone["sub"], f"zone '{zone['heading']}' on panel {index} ({name}) carries no sub-line"
            assert zone["shown"] and zone["box"]["width"] > 0 and zone["box"]["height"] > 0, (
                f"zone '{zone['heading']}' on panel {index} ({name}) is not being drawn: {zone['box']}"
            )
        fields = [field["id"] for zone in zones for field in zone["fields"]]
        assert len(fields) == 5, (
            f"panel {index} ({name}) shows {len(fields)} fields rather than the five "
            f"step 3 asks for: {fields}"
        )


def test_a_leaf_cards_fields_span_the_card(step_three):
    """No dead column on a leaf card, and no crushed controls inside the live one.

    **This is the measurement the defect was found by.** `.amount-grid--leaf`
    existed only in `calculator.js`; the stylesheet had no rule for it anywhere,
    so the card inherited `.amount-grid`'s `1fr 1fr` template while holding a
    single child. On a 960px card that gave the mass group 445px and left the
    other 445px **dead**, with the three required controls crushed into
    130/150/130px inside the live half. Every class name in that markup was
    present and spelled correctly throughout, which is why this test measures
    boxes: a version of it that asserted `.zones` or `.zone` were on the element
    would have passed against the broken stylesheet without complaint.

    Both halves of the defect are pinned separately, because they can come back
    separately: the zones must between them reach both edges of the card's
    content box, and no control inside the card may be crushed.

    Mutation: `.leaf-panel .zones { grid-template-columns: minmax(0,1fr)
    minmax(0,1fr) minmax(0,1fr) }` - two zones in a three-track grid, the exact
    shape of the original bug one level along - fails both assertions, naming
    the pixels left dead and the width the unit control shrank to.
    """
    page = step_three(1278, 983, leaves=3)
    panels = page.evaluate(PANELS)

    for index, panel in enumerate(panels):
        name = panel["legend"] or f"panel {index}"
        content_start = panel["contentStart"]
        content_end = panel["contentEnd"]
        content_width = content_end - content_start
        zones = panel["zones"]
        assert len(zones) == 2, f"{name}: {len(zones)} zones, cannot measure the span"

        start_gap = round(zones[0]["box"]["x"] - content_start)
        end_gap = round(content_end - zones[-1]["box"]["right"])
        between = round(zones[1]["box"]["x"] - zones[0]["box"]["right"])
        assert start_gap <= 2 and end_gap <= 2, (
            f"{name}: the zones do not reach the card's edges - {start_gap}px of the "
            f"card is unused before the first zone and {end_gap}px after the last, on a "
            f"content box {round(content_width)}px wide. That empty strip is the dead "
            f"column this layout was rebuilt to remove"
        )
        assert between <= 30, (
            f"{name}: the two zones are {between}px apart inside a "
            f"{round(content_width)}px card, which is a dead column between them rather "
            f"than the 24px gutter `.zones` declares"
        )

        #: The zones reaching both edges is only half of "the fields span the
        #: card" - a zone the full width of the card holding fields sixty per
        #: cent of it would leave the same strip of nothing, one level in. 34px
        #: is the zone's own 16px padding on each side plus its 1px hairline.
        narrow = [
            (field["id"], field["box"]["width"], zone["box"]["width"])
            for zone in zones
            for field in zone["fields"]
            if field["box"]["width"] < zone["box"]["width"] - 40
        ]
        assert not narrow, (
            f"{name}: {narrow} - a field stops short of the zone holding it "
            f"(id, field width, zone width). The zone reaches the card's edge and the "
            f"empty strip simply moved inside it"
        )

        controls = [
            (field["id"], field["controlWidth"])
            for zone in zones
            for field in zone["fields"]
        ]
        crushed = [pair for pair in controls if pair[1] is None or pair[1] < 180]
        assert not crushed, (
            f"{name}: {crushed} - a control is crushed inside a {round(content_width)}px "
            f"card. The layout this replaced squeezed step 3's three required controls "
            f"into 130/150/130px beside 445px of nothing"
        )


def test_the_two_zones_are_the_same_height_above_the_breakpoint(step_three):
    """Side by side, the two tinted boxes end level.

    **A deliberate reversal, and the reasoning it reverses is in the file.** The
    layout this replaced carried `align-items: start`, justified by the money
    group being allowed to be "left entirely empty and not padded out to look
    otherwise". That reads correctly when the two groups are bare text on the
    card's own white: a short column beside a long one reads as nothing at all.
    It stops being true the moment each group is a bordered, filled box - a box
    that stops short of its neighbour reads as a container of the wrong size,
    not as an honest absence - and the second group is never empty now, because
    it always carries a heading, a sub-line and two labelled inputs. Stretching
    costs no height either: the row was already as tall as the taller zone.

    Above the breakpoint only. Below it the zones stack, one after the other,
    and their heights have no reason to match.

    Mutation: putting `align-items: start` back on `.zones` in the 650px media
    rule leaves the supporting-figures zone about 137px short of its neighbour,
    and this fails naming both heights.
    """
    page = step_three(1278, 983, leaves=3)
    panels = page.evaluate(PANELS)

    for index, panel in enumerate(panels):
        name = panel["legend"] or f"panel {index}"
        zones = panel["zones"]
        assert len(zones) == 2, f"{name}: {len(zones)} zones, cannot compare two heights"
        assert zones[0]["box"]["top"] == zones[1]["box"]["top"], (
            f"{name}: the two zones do not start on the same line - "
            f"{zones[0]['heading']} at y={zones[0]['box']['top']}, "
            f"{zones[1]['heading']} at y={zones[1]['box']['top']}"
        )
        first, second = zones[0]["box"]["height"], zones[1]["box"]["height"]
        assert abs(first - second) <= 1, (
            f"{name}: the two tinted zones end at different heights - "
            f"'{zones[0]['heading']}' is {first}px tall and '{zones[1]['heading']}' is "
            f"{second}px. A filled, bordered box that stops short of the one beside it "
            f"reads as a container the wrong size, not as an empty one"
        )


def test_the_two_money_fields_do_not_repeat_one_hint(step_three):
    """Each NZ$ field says something the other does not.

    The defect: both carried *"For statistics only - it never enters the
    emissions calculation."*, the identical sentence, verbatim, one under the
    other. That fact is about both fields, so the zone's own sub-line says it
    once over the pair now; what each field's hint has to carry instead is the
    thing that differs between them, which is *what to put in the box*.

    Neither may be blank either. Every other field on this step carries a hint,
    and decision 6 of the zoning plan is that nothing essential lives behind a
    hover - a bare label here would have put "which price?" inside the tooltip
    and nowhere else.

    Mutation: giving both fields the same hint string in `leafPanel` fails
    naming the repeated sentence.
    """
    page = step_three(1278, 983, leaves=3)
    panels = page.evaluate(PANELS)

    for index, panel in enumerate(panels):
        name = panel["legend"] or f"panel {index}"
        money = panel["zones"][1]["fields"]
        assert len(money) == 2, f"{name}: the supporting zone holds {len(money)} fields, not two"
        for field in money:
            assert field["hint"], (
                f"{name}: the field '{field['id']}' carries no hint at all, so what to put "
                f"in it is stated only inside a tooltip nobody is told to open"
            )
        first, second = money[0]["hint"], money[1]["hint"]
        assert first != second, (
            f"{name}: both NZ$ fields carry the same hint, word for word - "
            f"'{first}'. Saying one fact twice leaves the thing that differs between "
            f"the two boxes - which price goes in which - unsaid"
        )


def test_the_money_examples_are_a_format_the_field_accepts(step_three):
    """**The example must be typable into the box it sits in.**

    Both NZ$ inputs are `type="number"`, and a number input refuses a thousands
    separator outright: type `50,000` into one and it holds nothing. PR #101
    proposed the placeholders as `e.g. 50,000` and `e.g. 1,200`; the idea was
    right and the digits were not. A placeholder demonstrating a format the
    control rejects is worse than an empty one, because a visitor who copies it
    watches the field stay empty and has nothing telling them why.

    So this asserts the example is *present*, is *not* the same in both boxes,
    and would *survive being typed* — which is the assertion that would have
    caught #101's version, and which "a placeholder exists" would not.

    Mutation: putting a comma back into either example fails naming it.
    """
    page = step_three(1278, 983, leaves=3)
    measured = page.evaluate(
        """() => [...document.querySelectorAll('.leaf-panel')].map(panel => ({
          legend: panel.querySelector('legend')?.innerText.trim(),
          money: [...panel.querySelectorAll('.zone:nth-of-type(2) input')].map(input => ({
            id: input.id,
            placeholder: input.getAttribute('placeholder') || '',
            type: input.type,
            //: The browser's own verdict, not a regex of ours: assigning the
            //: example and reading it back is exactly what happens when a
            //: visitor types it.
            survivesTyping: (() => {
              const probe = document.createElement('input')
              probe.type = input.type
              probe.value = (input.getAttribute('placeholder') || '').replace(/^[^0-9]*/, '')
              return probe.value
            })(),
          })),
        }))"""
    )

    for index, panel in enumerate(measured):
        name = panel["legend"] or f"panel {index}"
        assert len(panel["money"]) == 2, f"{name}: expected two NZ$ inputs, found {len(panel['money'])}"
        for field in panel["money"]:
            assert field["placeholder"], (
                f"{name}: '{field['id']}' has no example value, so nothing on screen shows "
                f"the shape of the number this box wants"
            )
            assert field["survivesTyping"], (
                f"{name}: '{field['id']}' offers the example "
                f"'{field['placeholder']}', which an `input[type={field['type']}]` refuses - "
                f"typed in, it leaves the box empty. An example the control will not accept "
                f"teaches the wrong format"
            )
        first, second = (field["placeholder"] for field in panel["money"])
        assert first != second, (
            f"{name}: both NZ$ boxes show the same example '{first}'. Two figures of the "
            f"same order read as one of them being a copy of the other"
        )

# ------------------------------------------------------------------- the tooltip


def test_the_term_carries_a_dotted_underline(step_three):
    """The affordance, without which the explanations may as well not exist.

    The client's call was that the trigger is the field name itself rather than
    an `i` beside it, and `text-decoration: underline dotted` is then the whole
    of what tells a reader there is anything to hover. This is why that
    declaration is not decoration in the droppable sense: remove it and four
    written explanations are still in the DOM, still reachable by `Tab`, and
    invisible to every visitor who does not already know they are there.

    Mutation: `.term { text-decoration: none }` fails naming the term whose
    underline went, and what was computed instead.
    """
    page = step_three(1278, 983, leaves=1)
    terms = page.evaluate(
        """() => [...document.querySelectorAll('.zone .term')].map(term => {
             const style = getComputedStyle(term);
             return {
               text: term.firstChild ? term.firstChild.textContent.trim() : null,
               line: style.textDecorationLine,
               style: style.textDecorationStyle,
             };
           })"""
    )
    assert len(terms) == 4, f"step 3 draws {len(terms)} explained terms rather than four: {terms}"
    for term in terms:
        assert "underline" in term["line"] and term["style"] == "dotted", (
            f"the term '{term['text']}' has no dotted underline - computed "
            f"text-decoration-line '{term['line']}', style '{term['style']}'. Without it "
            f"nothing on the label says an explanation is there to be opened"
        )


def test_the_term_tooltip_opens_on_hover(step_three):
    """Path one of three: a pointer resting on the word.

    Held up by `.term:hover > .tip` and nothing else.

    Mutation: dropping `.term:hover` from that selector list leaves the panel
    `hidden` under the pointer, and this fails naming the computed visibility.
    """
    page = step_three(1278, 983, leaves=1)
    selector = _tip_selector("total-waste")

    before = page.evaluate(TIP_STATE, selector)
    assert before is not None, "the waste amount's label carries no tooltip panel at all"
    assert before["visibility"] == "hidden", (
        f"the tooltip is already showing before anything has touched it: {before}"
    )

    _term(page, "total-waste").hover()
    page.wait_for_timeout(250)
    hovered = page.evaluate(TIP_STATE, selector)
    assert hovered["visibility"] == "visible" and hovered["opacity"] == "1", (
        f"the tooltip did not open under the pointer: computed visibility "
        f"'{hovered['visibility']}', opacity {hovered['opacity']}. A pointer resting on "
        f"a dotted-underlined term is the one way in every sighted mouse user will try"
    )


def test_the_term_tooltip_opens_on_a_real_tab_press(step_three):
    """Path two of three: the keyboard, driven by an actual `Tab`.

    **A real key press rather than `element.focus()`**, deliberately. `.focus()`
    would prove the CSS matches a focused span and nothing else; it would stay
    green if the term lost `tabindex="0"` and became unreachable by keyboard
    altogether, which is the failure a keyboard user would actually meet. The
    press starts from the unit select, the control immediately before the waste
    amount's label in DOM order, so what is being asserted is that one `Tab`
    from the previous control lands on the term.

    `:focus-within` rather than `:focus-visible` is what makes this work at all,
    and is also what makes the tap path below possible - a browser withholds
    `:focus-visible` from pointer-driven focus.

    Mutation: removing `tabindex="0"` from `term()` leaves the `Tab` on the
    waste-amount input instead, and this fails naming what took focus.
    """
    page = step_three(1278, 983, leaves=1)
    selector = _tip_selector("total-waste")

    page.evaluate("() => document.getElementById('total-unit').focus()")
    page.wait_for_timeout(100)
    page.keyboard.press("Tab")
    page.wait_for_timeout(250)

    focused = page.evaluate(TIP_STATE, selector)
    assert focused["activeIsTheTerm"], (
        f"one Tab from the unit control did not reach the waste amount's term - focus is "
        f"on '{focused['activeDescription']}'. A term that cannot be reached by keyboard "
        f"has no keyboard path to its explanation, whatever the CSS says"
    )
    assert focused["visibility"] == "visible" and focused["opacity"] == "1", (
        f"the term has keyboard focus but its tooltip stayed shut: computed visibility "
        f"'{focused['visibility']}', opacity {focused['opacity']}"
    )


def test_the_term_tooltip_opens_on_a_tap(step_three):
    """Path three of three, and the one where the obvious test is a false green.

    **Read this before changing it.** The naive version - emulate a touch, tap
    the term, assert the tooltip is showing - **passes with the fix reverted**.
    Reproduced twice: with `preventDefault` deleted and the image rebuilt, a
    mouse `click()` correctly left the panel `hidden`, and a touch `tap()` left
    it `visible`. Chromium's touch emulation leaves a sticky compatibility
    `:hover` on the tapped element, and a synthetic mouse move away does not
    clear it, because they are separate CDP input sources. So a touch-driven
    test is held up by `:hover` and never exercises the line it claims to.

    What discriminates is a **mouse** `click()` at a phone viewport followed by
    `mouse.move(5, 5)`: the pointer is then demonstrably elsewhere, `:hover`
    cannot be holding the panel open, and only focus left on the span can be.
    Without `preventDefault` the label forwards the activation to its input, the
    span blurs, `:focus-within` stops matching and the panel shuts in the same
    frame it opened.

    The touch leg is kept after it, asserting the **one thing touch does
    discriminate on**: that the tap left focus on the term rather than on the
    input the label points at. It is deliberately not asserting visibility,
    because visibility is exactly what touch cannot tell you here.

    Mutation: deleting `if (event.target.closest('.term')) event.preventDefault()`
    from `bindCalculator`'s click listener fails the first assertion, naming the
    computed visibility and where focus went.
    """
    page = step_three(390, 700, leaves=1, has_touch=True)
    selector = _tip_selector("total-waste")

    _term(page, "total-waste").click()
    page.mouse.move(5, 5)
    page.wait_for_timeout(300)
    clicked = page.evaluate(TIP_STATE, selector)
    assert clicked["visibility"] == "visible" and clicked["opacity"] == "1", (
        f"tapping the term did not leave its tooltip open: computed visibility "
        f"'{clicked['visibility']}', opacity {clicked['opacity']}, focus on "
        f"'{clicked['activeDescription']}'. The pointer was moved to (5, 5) first, so "
        f"nothing is hovering it - a term inside a <label> hands its activation to the "
        f"label's control unless the click handler refuses it, and the panel shuts in "
        f"the same frame it opened. On a phone that is the only way in there is"
    )

    _term(page, "total-waste").tap()
    page.wait_for_timeout(300)
    tapped = page.evaluate(TIP_STATE, selector)
    assert tapped["activeIsTheTerm"], (
        f"a real touch tap on the term put focus on '{tapped['activeDescription']}' "
        f"instead of on the term itself, so `:focus-within` has nothing to match and "
        f"the panel is held open only by an emulated hover a real finger does not leave"
    )


def test_the_term_tooltip_closes_again(step_three):
    """It shuts when the pointer leaves and focus moves away.

    **Without this every visibility assertion in this file is vacuous.** A panel
    hard-coded `visible` would satisfy hover, keyboard and tap alike; what makes
    those three tests mean anything is that the closed state is the default and
    is returned to. Both exits are driven, because two different selectors hold
    it open: the pointer leaves for `:hover`, and focus moves to another control
    for `:focus-within`.

    Mutation: `.term > .tip { visibility: visible }` unconditionally fails at the
    first exit, and a mutation that removes only one of the two selectors fails
    at whichever exit it owns.
    """
    page = step_three(1278, 983, leaves=1)
    selector = _tip_selector("total-waste")

    _term(page, "total-waste").hover()
    page.wait_for_timeout(250)
    assert page.evaluate(TIP_STATE, selector)["visibility"] == "visible", (
        "the tooltip did not open on hover, so there is nothing to watch close"
    )
    page.mouse.move(5, 5)
    page.wait_for_timeout(250)
    unhovered = page.evaluate(TIP_STATE, selector)
    assert unhovered["visibility"] == "hidden" and unhovered["opacity"] == "0", (
        f"the tooltip stayed open after the pointer left the term: computed visibility "
        f"'{unhovered['visibility']}', opacity {unhovered['opacity']}. A panel that never "
        f"closes covers the field under it for the rest of the step"
    )

    page.evaluate("() => document.querySelector('label[for=\"total-waste\"] .term').focus()")
    page.wait_for_timeout(250)
    assert page.evaluate(TIP_STATE, selector)["visibility"] == "visible", (
        "the tooltip did not open on focus, so there is nothing to watch close"
    )
    page.evaluate("() => document.getElementById('total-input').focus()")
    page.mouse.move(5, 5)
    page.wait_for_timeout(250)
    blurred = page.evaluate(TIP_STATE, selector)
    assert blurred["visibility"] == "hidden" and blurred["opacity"] == "0", (
        f"the tooltip stayed open after focus moved on to another control: computed "
        f"visibility '{blurred['visibility']}', opacity {blurred['opacity']}, focus now on "
        f"'{blurred['activeDescription']}'"
    )


@pytest.mark.parametrize("width", (320, 390))
def test_an_open_tooltip_stays_inside_the_viewport(step_three, width):
    """The panel fits the screen at a phone width, and the page never scrolls sideways.

    **Measured closed as well as open, which is not belt and braces.** The panel
    is `position: absolute` with `visibility: hidden` when shut, and a hidden
    absolutely-positioned box still contributes to `scrollWidth` - so a panel
    sized wider than its container would put a sideways scrollbar on step 3 for
    every visitor who never hovers anything at all. That is the defect
    `.result-explanation__body` already records one screen over: anchored to its
    own short inline trigger, a panel has no relation to the room available. Here
    the containing block is the **label**, which spans the zone's content width
    exactly, so the panel cannot leave the screen in either direction by
    construction - and this is what says so rather than assuming it.

    All four terms, because they sit at four different heights and the widest
    copy is not in the first one.

    Mutation: `.term > .tip { min-inline-size: 420px }` - a fixed minimum wider
    than the zone, which is precisely what the prototype's `max(250px, 100%)`
    amounted to at 320px - fails naming the term, the right edge it reached and
    the viewport it left.
    """
    page = step_three(width, 700, leaves=1)
    controls = ("total-waste", "total-input", "total-value", "wasted-value")

    shut = page.evaluate(TIP_STATE, _tip_selector("total-waste"))
    allowed = max(shut["clientWidth"], MIN_CONTENT_WIDTH)
    assert shut["scrollWidth"] <= allowed, (
        f"step 3 scrolls sideways at {width}px with every tooltip still shut: scrollWidth "
        f"{shut['scrollWidth']} against a content width of {allowed}. A hidden "
        f"absolutely-positioned panel still counts towards scrollWidth, so an oversized "
        f"one costs a sideways scrollbar to visitors who never open it"
    )

    for control in controls:
        page.evaluate(
            "id => document.querySelector(`label[for=\"${id}\"] .term`).focus()", control
        )
        page.wait_for_timeout(200)
        state = page.evaluate(TIP_STATE, _tip_selector(control))
        assert state["visibility"] == "visible", (
            f"the tooltip on '{control}' did not open at {width}px, so its fit was never measured"
        )
        assert state["left"] >= 0 and state["right"] <= state["innerWidth"], (
            f"the tooltip on '{control}' hangs outside a {width}px screen: it runs from "
            f"x={state['left']} to x={state['right']} in a viewport {state['innerWidth']}px "
            f"wide. Nothing scrolls to recover it - the panel is simply off the screen"
        )
        assert state["scrollWidth"] <= max(state["clientWidth"], MIN_CONTENT_WIDTH), (
            f"opening the tooltip on '{control}' made step 3 scroll sideways at {width}px: "
            f"scrollWidth {state['scrollWidth']} against a content width of "
            f"{max(state['clientWidth'], MIN_CONTENT_WIDTH)}"
        )


def test_every_term_describes_exactly_one_tooltip_across_a_forked_chain(step_three):
    """`aria-describedby` resolves, once, and no two leaves share an id.

    **Measured on three leaves, never on one.** A single leaf would be green
    against ids hard-coded in `term()`: it is the second and third cards that
    turn one duplicated id into three elements answering to it, at which point
    every screen reader on the chain is read the *first* card's explanation for
    the third card's field. `fieldId()` is what suffixes them per leaf, and this
    is the test that notices if a term stops going through it.

    The whole document's ids are swept, not just the tips': a tip id colliding
    with some unrelated control would break the reference just as thoroughly.

    Mutation: dropping the `fieldId()` call in `leafPanel` so the four tip ids
    are bare constants fails with each of the four naming three matches, and the
    duplicate sweep names all twelve.
    """
    page = step_three(1278, 983, leaves=3)
    measured = page.evaluate(
        """() => {
          const ids = [...document.querySelectorAll('[id]')].map(el => el.id);
          const seen = new Set();
          const duplicates = new Set();
          for (const id of ids) { if (seen.has(id)) duplicates.add(id); seen.add(id); }
          return {
            duplicates: [...duplicates],
            terms: [...document.querySelectorAll('.zone .term')].map(term => {
              const target = term.getAttribute('aria-describedby');
              return {
                text: term.firstChild ? term.firstChild.textContent.trim() : null,
                target,
                matches: target ? document.querySelectorAll('[id="' + target + '"]').length : 0,
                ownsIt: !!(target && term.querySelector('[id="' + target + '"]')),
                role: target && document.getElementById(target)
                  ? document.getElementById(target).getAttribute('role') : null,
                body: target && document.getElementById(target)
                  ? document.getElementById(target).textContent.trim() : '',
              };
            }),
          };
        }"""
    )

    assert len(measured["terms"]) == 12, (
        f"three leaves drew {len(measured['terms'])} explained terms rather than twelve "
        f"(four per card): {[term['text'] for term in measured['terms']]}"
    )
    assert not measured["duplicates"], (
        f"the step-3 chain repeats {len(measured['duplicates'])} id(s) across its leaf "
        f"cards: {measured['duplicates']}. A duplicated id makes every reference to it "
        f"resolve to the first card's copy, whichever card the visitor is on"
    )
    for term in measured["terms"]:
        assert term["target"], f"the term '{term['text']}' carries no aria-describedby"
        assert term["matches"] == 1, (
            f"the term '{term['text']}' points at id '{term['target']}', which "
            f"{term['matches']} elements answer to. Assistive technology reads the first "
            f"one, so on a forked chain that is another leaf's explanation"
        )
        assert term["ownsIt"], (
            f"the term '{term['text']}' points at '{term['target']}', which is not its "
            f"own panel - the id has drifted onto some other element"
        )
        assert term["role"] == "tooltip", (
            f"the panel '{term['target']}' described by '{term['text']}' has role "
            f"'{term['role']}' rather than 'tooltip'"
        )
        assert term["body"], (
            f"the tooltip '{term['target']}' on '{term['text']}' is empty, so the term "
            f"is underlined as if it explained something and explains nothing"
        )
