"""Step 5's clock, driven by a real pointer and a real keyboard (WP3).

**A pointer test that never dispatches a pointer event proves nothing.** The
angle arithmetic is enumerated under Node in `tests/web/test_period_rules.py`,
where ninety-six ring-and-angle cases cost nothing; what cannot be asked there is
whether a *drag* works — whether `setPointerCapture` keeps delivering events once
the finger leaves the circle, whether the hand survives the stage advancing,
whether the value the visitor released on is the value that reaches the box. So
every gesture below is `page.mouse.down`/`move`/`up` at measured coordinates, and
the assertions are about what the face and the field then read.

Four claims this file exists to hold, because nothing else in the suite can:

* **the opening-mode rule.** A pointer press opens the dial and a keyboard press
  opens the two number boxes, on `event.detail === 0`. That is one character
  carrying the whole of the control's accessibility story, and it is only
  observable from a browser that synthesises the click itself.
* **a drag across twelve o'clock**, which is where a wrapped angle goes wrong.
* **the two rings and the one-minute resolution**, at real coordinates on a real
  face rather than in the abstract.
* **the face does not mirror under `dir="rtl"`**, deliberately unlike the
  calendar one field over. Asserted in Arabic, because "SVG does not mirror" is
  exactly the kind of claim that is true until a stylesheet makes it false.

**Running these.** As for `test_period_picker_browser.py`: `web/` is baked into
the image, so without a `build web` the browser is served the last image's
JavaScript and every measurement below is of code that is not on disk::

    docker compose -f docker/compose.yaml build web
    docker compose -f docker/compose.yaml up -d --force-recreate web
    pytest tests/web/test_period_clock_browser.py
"""

from __future__ import annotations

import datetime as dt
import math
import os

import pytest

from tests.web.steps import press_continue

pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the clock with a pointer; the dial is unverified without it",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080/index.html")

CALCULATE = '.step-nav [data-action="calculate"]'

#: The readout's two stage buttons, as `[stage, aria-pressed, text]`.
STAGES = (
    '[...document.querySelectorAll(\'[data-action="period-clock-stage"]\')]'
    ".map(button => [button.dataset.stage, button.getAttribute('aria-pressed'), button.textContent])"
)


@pytest.fixture
def review(browser):
    """A page standing on step 5 with *Custom period* chosen, in a named language.

    A fixture rather than a helper so every context closes with the test that
    opened it: §6.5 caps a caller at 600 GETs an hour and each page load spends
    one on `/taxonomy`, which is the budget the whole of `tests/web` lives
    inside.
    """
    contexts = []

    def open_page(lang="en", width=1278, height=983, reduced_motion=None, time_frame="custom"):
        options = {"viewport": {"width": width, "height": height}, "locale": "en-NZ"}
        if reduced_motion is not None:
            options["reduced_motion"] = reduced_motion
        context = browser.new_context(**options)
        contexts.append(context)
        page = context.new_page()
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.uncaught = failures
        try:
            page.goto(f"{BASE}?lang={lang}", wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {BASE}: {error}")
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        page.click('[data-action="start"]')
        page.wait_for_selector('input[name="sector"]')
        page.locator('input[name="sector"]').first.check()
        press_continue(page)
        press_continue(page)
        page.wait_for_selector("#total-waste")
        page.fill("#total-waste", "1000")
        press_continue(page)
        page.wait_for_selector('[data-line-field="amount"]')
        page.locator('[data-line-field="amount"]').first.fill("500")
        press_continue(page)
        page.wait_for_selector("#time-frame", timeout=10000)
        page.select_option("#time-frame", time_frame)
        page.wait_for_timeout(180)
        return page

    yield open_page
    for context in contexts:
        context.close()


def _open_dial(page, bound="start"):
    """Open the clock the way a mouse does. Playwright's `click` dispatches a
    real press, so `event.detail` is 1 and the dial is what comes up."""
    page.click(f"#period-clock-{bound}")
    page.wait_for_selector("#period-clock-dialog", timeout=5000)
    page.wait_for_selector(".period-clock-face", timeout=5000)
    page.wait_for_timeout(150)


def _face(page):
    """The face's centre and radius, in viewport pixels.

    Measured on every call rather than cached: the dialog is centred in the
    viewport and the value is what every gesture below is aimed with.
    """
    box = page.locator(".period-clock-face").bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, box["width"] / 2


def _at(page, degrees, fraction=0.85):
    """A viewport point `degrees` clockwise from twelve, `fraction` of the way
    out. The inverse of `period.js`'s `facePoint`, written independently so the
    test does not borrow the convention it is checking."""
    cx, cy, radius = _face(page)
    radians = math.radians(degrees)
    return cx + radius * fraction * math.sin(radians), cy - radius * fraction * math.cos(radians)


def _tap(page, degrees, fraction=0.85):
    x, y = _at(page, degrees, fraction)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.up()
    page.wait_for_timeout(200)


def _announced(page):
    return page.evaluate("document.querySelector('#period-clock-value')?.textContent")


def _labels(page):
    """Every label on the face, by text, as the centre of its box in viewport
    pixels."""
    return page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('.period-clock-label')].map(label => {
             const rect = label.getBoundingClientRect();
             return [label.textContent, [Math.round(rect.x + rect.width / 2), Math.round(rect.y + rect.height / 2)]];
           }))"""
    )


# ---------------------------------------------------------------------------
# Additive: nothing that worked yesterday stopped working
# ---------------------------------------------------------------------------


def test_the_text_box_is_still_the_fast_path_and_the_button_opens_nothing_by_itself(review):
    """**Decision 3: the clock is an addition, not a replacement.**

    Three things have to be true together, and a test of the dial alone would
    say nothing about any of them: `0810` still becomes `08:10` in the box, the
    clock button is a tab stop *after* it rather than in front of it, and tabbing
    past that button does not open a dialog. The last is the one a visitor who
    never wants the dial actually depends on.
    """
    page = review()
    page.click("#period-start-time")
    page.keyboard.type("0810")
    page.wait_for_timeout(150)
    assert page.locator("#period-start-time").input_value() == "08:10", (
        "the time box stopped punctuating itself when the clock button moved in beside it"
    )

    page.keyboard.press("Tab")
    page.wait_for_timeout(150)
    assert page.evaluate("document.activeElement?.id") == "period-clock-start", (
        "the clock button is not the next tab stop after the time box it belongs to"
    )
    page.keyboard.press("Tab")
    page.wait_for_timeout(200)
    assert page.locator("#period-clock-dialog").count() == 0, (
        "tabbing past the clock button opened the dialog; the button is a control, "
        "not a trap"
    )
    assert page.locator("#period-start-time").input_value() == "08:10"
    assert not page.uncaught, page.uncaught


def test_there_is_a_clock_beside_each_time_box_and_a_calendar_beside_each_date_box(review):
    """The pairing is the whole of the visual argument: a visitor learns one
    button and gets two. Both buttons carry an `aria-label` and an inline SVG
    marked `aria-hidden` — a second accessible name inside the button would be
    read twice, which is what `CALENDAR_ICON`'s own note says."""
    page = review()
    clocks = page.locator('[data-action="period-clock-open"]')
    assert clocks.count() == 2, "one clock per bound"
    assert [clocks.nth(index).get_attribute("aria-label") for index in range(2)] == [
        "Choose the start time",
        "Choose the end time",
    ]
    assert page.evaluate(
        "[...document.querySelectorAll('[data-action=\"period-clock-open\"] svg')]"
        ".every(svg => svg.getAttribute('aria-hidden') === 'true')"
    ), "the icon inside the button is not hidden from assistive technology"
    assert page.evaluate(
        "[...document.querySelectorAll('[data-action=\"period-clock-open\"]')]"
        ".every(button => button.getAttribute('aria-haspopup') === 'dialog')"
    )
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# Which mode opens, and why
# ---------------------------------------------------------------------------


def test_a_pointer_press_opens_the_dial_and_a_keyboard_press_opens_the_number_boxes(review):
    """**The one-character condition, pressed both ways in one browser.**

    `event.detail` is the click count: 1 for a real press and 0 for a click the
    browser synthesised from `Enter` or `Space` on a focused button. A dial has
    no keyboard route into it at all, so a keyboard user who was handed the face
    would have opened a control they cannot drive and cannot hear; a pointer user
    handed two spinners would have lost the thing they reached for.

    Both `Enter` and `Space` are pressed, because they are two separate
    activation paths in the HTML specification and a browser is free to give them
    different `detail` values — which is exactly the sort of thing that is
    assumed rather than measured.
    """
    page = review()

    page.click("#period-clock-start")
    page.wait_for_timeout(220)
    assert page.locator(".period-clock-face").count() == 1, (
        "a mouse press did not open the dial"
    )
    assert page.locator("#period-clock-hour").count() == 0
    assert page.evaluate("document.activeElement?.id") == "period-clock-stage", (
        "focus did not move into the dialog; Esc and the Tab cycle are both "
        "unreachable from outside it"
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    for key in ("Enter", "Space"):
        page.focus("#period-clock-start")
        page.keyboard.press(key)
        page.wait_for_timeout(220)
        assert page.locator("#period-clock-hour").count() == 1, (
            f"{key} on the clock button opened something other than the keyboard mode"
        )
        assert page.locator(".period-clock-face").count() == 0, (
            f"{key} opened the dial, which a keyboard cannot drive"
        )
        assert page.evaluate("document.activeElement?.id") == "period-clock-hour"
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    assert not page.uncaught, page.uncaught


def test_the_toggle_names_the_mode_it_switches_to_and_reaches_both_ways(review):
    """A button labelled with the mode it is *in* is a label a visitor reads as a
    statement and presses expecting nothing to happen. It also has to work in
    both directions, or one of the two modes is a place somebody can be
    stranded — which for the keyboard mode would mean stranded with no way
    back."""
    page = review()
    _open_dial(page)
    toggle = page.locator('[data-action="period-clock-mode"]')
    assert toggle.inner_text().strip() == "Enter the time on a keyboard"
    toggle.click()
    page.wait_for_timeout(220)
    assert page.locator("#period-clock-hour").count() == 1
    assert toggle.inner_text().strip() == "Choose the time on a clock face"
    toggle.click()
    page.wait_for_timeout(220)
    assert page.locator(".period-clock-face").count() == 1, (
        "the toggle does not come back from the keyboard mode"
    )
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# The dial, driven by a pointer
# ---------------------------------------------------------------------------


def test_a_drag_across_twelve_oclock_does_not_wrap_the_wrong_way(review):
    """**The gesture an angle gets wrong**, and the reason the drag is driven
    rather than the handler inspected.

    A sweep from ten o'clock through twelve to two crosses the 0°/360° seam. An
    arithmetic that clamped instead of wrapping would stick at 11 or jump to 11
    o'clock going backwards; one that wrapped the wrong way would run 10, 11, 12,
    11, 10. The value is read off the live region at every step, so what is
    asserted is the whole path and not only where it stopped.

    The `pointerup` is deliberately released at 1.4 of the radius — well outside
    the face — and the value still lands. **That is not, on its own, evidence of
    the pointer capture**, and the claim that it is was measured and found false:
    see `test_the_face_holds_the_pointer_capture_while_a_drag_is_in_flight`.
    """
    page = review()
    _open_dial(page)
    start = _at(page, 300)
    page.mouse.move(*start)
    page.mouse.down()
    seen = []
    for degrees in range(300, 421, 10):
        page.mouse.move(*_at(page, degrees % 360))
        page.wait_for_timeout(25)
        seen.append(_announced(page).split(":")[0])
    page.mouse.move(*_at(page, 60, fraction=1.4))
    page.wait_for_timeout(40)
    page.mouse.up()
    page.wait_for_timeout(250)

    hours = [int(value) for value in seen]
    assert hours[0] == 10 and hours[-1] == 2, f"the sweep did not run 10 -> 2: {seen}"
    assert hours == sorted(hours, key=lambda value: value if value >= 10 else value + 12), (
        f"the hand went backwards across twelve o'clock: {seen}"
    )
    assert 12 in hours, f"the sweep skipped twelve o'clock entirely: {seen}"
    assert _announced(page).startswith("02:"), (
        f"the release outside the face was lost: {_announced(page)} — this is what "
        f"pointer capture exists for"
    )
    assert not page.uncaught, page.uncaught


def test_the_face_holds_the_pointer_capture_while_a_drag_is_in_flight(review):
    """**A survivor, and the honest way to close it.**

    Deleting `setPointerCapture` from `handlePeriodPointer` left every other test
    in this file green, and the reason is measured rather than guessed: the
    dialog's backdrop is `position: fixed; inset: 0`, it is what
    `document.elementFromPoint` returns at *every* point in the viewport
    including (2, 2), and it is a DOM descendant of `main` — which is where all
    four pointer listeners are delegated, because `render()` replaces
    `main.innerHTML`. So a `pointermove` a long way outside the face bubbles to
    the same handler whether or not anything captured the pointer. Releasing the
    capture from the page mid-drag and then moving to 1.6 × the radius still
    tracked the hand and still advanced the stage.

    **That makes the mutation equivalent, not the drag test weak** — no gesture
    a browser test can perform distinguishes the two, because the one case that
    does is a pointer released outside the browser window, which Playwright
    cannot reach. So the capture is asserted directly, through the browser's own
    `hasPointerCapture`, which is observable state rather than a read of the
    source.

    It is worth keeping and worth pinning because the equivalence is a fact about
    a *stylesheet*: it holds only while the backdrop covers the viewport and
    lives inside `main`, both of which are one `inset` or one `popover` away from
    being false, and a gesture that broke then would be a long way from the
    change that broke it.
    """
    page = review()
    _open_dial(page)
    assert page.evaluate(
        """() => { const backdrop = document.elementFromPoint(2, 2);
             return [backdrop?.className, document.querySelector('main').contains(backdrop)]; }"""
    ) == ["period-dialog-backdrop", True], (
        "the backdrop no longer covers the viewport from inside `main`, so the "
        "delegated listeners no longer see a pointer outside the face and the "
        "capture below is now the only thing keeping a drag alive"
    )

    page.mouse.move(*_at(page, 240))
    page.mouse.down()
    page.wait_for_timeout(60)
    # Chromium gives a mouse `pointerId` of 1.
    held = page.evaluate("document.querySelector('.period-clock-face').hasPointerCapture(1)")
    page.mouse.up()
    page.wait_for_timeout(150)
    assert held, (
        "the face does not hold the pointer capture during a drag; a release "
        "outside the browser window would never be delivered, and the gesture "
        "would be depending on the backdrop's geometry instead"
    )
    assert not page.uncaught, page.uncaught


def test_a_tap_on_the_inner_ring_lands_on_thirteen_to_twenty_three_or_midnight(review):
    """**The reason there are two rings at all.** The form is on a 24-hour clock
    and half of its hours have nowhere to go on a twelve-position face.

    Three taps, at the same three angles on both rings, so what is being
    asserted is the *radius* and not the angle: straight up is 12 outside and 00
    inside, one o'clock is 1 and 13, and five o'clock is 5 and 17.
    """
    page = review()
    _open_dial(page)
    for degrees, outer, inner in ((0, "12", "00"), (30, "01", "13"), (150, "05", "17")):
        _tap(page, degrees, fraction=0.85)
        assert _announced(page).startswith(f"{outer}:"), (
            f"{degrees}° on the outer ring read {_announced(page)}, not {outer}"
        )
        page.locator('[data-action="period-clock-stage"][data-stage="hours"]').click()
        page.wait_for_timeout(180)
        _tap(page, degrees, fraction=0.45)
        assert _announced(page).startswith(f"{inner}:"), (
            f"{degrees}° on the inner ring read {_announced(page)}, not {inner}"
        )
        page.locator('[data-action="period-clock-stage"][data-stage="hours"]').click()
        page.wait_for_timeout(180)
    assert not page.uncaught, page.uncaught


def test_a_minute_that_is_not_a_multiple_of_five_can_be_chosen_and_is_what_is_written(review):
    """**08:07 is a real shift and a five-minute dial would refuse it.**

    Only every fifth minute is labelled — twelve labels, not sixty — and the
    obvious implementation snaps to them. This taps at 42°, which is seven
    minutes, and follows the value all the way into the field: a control that
    resolved to one minute on screen and rounded on the way out would be the same
    defect one step later.
    """
    page = review()
    _open_dial(page)
    _tap(page, 240)  # eight o'clock
    assert _announced(page).startswith("08:")
    assert page.evaluate("document.querySelectorAll('.period-clock-label').length") == 12, (
        "the minute face should carry twelve labels; the other forty-eight minutes "
        "are reachable and unlabelled on purpose"
    )
    _tap(page, 42)
    assert _announced(page) == "08:07", f"the minute ring snapped: {_announced(page)}"
    page.click('[data-action="period-clock-set"]')
    page.wait_for_timeout(250)
    assert page.locator("#period-start-time").input_value() == "08:07", (
        "a minute that is not a multiple of five was rounded on its way into the field"
    )
    assert not page.uncaught, page.uncaught


def test_the_hours_come_first_and_the_minutes_follow_and_there_is_a_way_back(review):
    """Two stages, the second following the first automatically — and the hour
    reachable again afterwards, because a visitor who set the wrong hour must not
    have to close the dialog and start over.

    The roving `id="period-clock-stage"` is what carries focus across the stage
    advance: `main.js` re-focuses `document.activeElement.id` after a same-step
    render, so an id that named the *hour* button would leave focus on the stage
    the visitor has just finished with.
    """
    page = review()
    _open_dial(page)
    opened = page.evaluate(STAGES)
    assert [stage[:2] for stage in opened] == [["hours", "true"], ["minutes", "false"]], (
        f"the clock did not open on the hours: {opened}"
    )

    _tap(page, 90)  # three o'clock
    stages = page.evaluate(STAGES)
    assert stages[0][1] == "false" and stages[1][1] == "true", (
        f"releasing the hour hand did not advance to the minutes: {stages}"
    )
    assert stages[0][2] == "03"
    assert page.evaluate("document.activeElement?.dataset?.stage") == "minutes", (
        "focus stayed on the stage the visitor had just finished with"
    )
    assert page.evaluate("document.querySelectorAll('.period-clock-label').length") == 12

    _tap(page, 180)  # half past
    assert _announced(page) == "03:30"
    assert page.evaluate(STAGES)[1][1] == "true", (
        "releasing on the minutes advanced somewhere; the value is complete and "
        "only Set the time may state it"
    )

    page.locator('[data-action="period-clock-stage"][data-stage="hours"]').click()
    page.wait_for_timeout(200)
    assert page.evaluate(STAGES)[0][1] == "true", "there is no way back to the hours"
    assert page.evaluate("document.querySelectorAll('.period-clock-label').length") == 24
    assert _announced(page) == "03:30", "going back to the hours discarded the minute"
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# The dialog's four obligations
# ---------------------------------------------------------------------------


def test_esc_closes_the_clock_and_returns_focus_to_the_button_that_opened_it(review):
    """What makes it a dialog rather than a panel. Focus goes back to the
    control that opened it — not to `<main>`, not to the top of the form — and
    nothing is written by closing."""
    page = review()
    page.fill("#period-start-time", "08:10")
    page.wait_for_timeout(150)
    _open_dial(page, "start")
    _tap(page, 90)
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    assert page.locator("#period-clock-dialog").count() == 0
    assert page.evaluate("document.activeElement?.id") == "period-clock-start"
    assert page.locator("#period-start-time").input_value() == "08:10", (
        "closing the clock wrote the hand's position into the field; only "
        "Set the time may state a value"
    )
    assert not page.uncaught, page.uncaught


def test_tab_from_the_last_control_does_not_leave_the_dialog(review):
    """`aria-modal` tells a screen reader the rest of the page is inert; it does
    not stop `Tab` reaching it. The cycle does — and it has to be the clock's own
    cycle, not the calendar's, or `Tab` walks out of one dialog into a dialog
    that is not on screen."""
    page = review()
    _open_dial(page)
    inside = (
        "document.getElementById('period-clock-dialog')"
        ".contains(document.activeElement)"
    )
    for _ in range(9):
        page.keyboard.press("Tab")
        page.wait_for_timeout(90)
        assert page.evaluate(inside), (
            "Tab left the clock dialog: "
            + str(page.evaluate("document.activeElement?.outerHTML?.slice(0, 90)"))
        )
    for _ in range(4):
        page.keyboard.press("Shift+Tab")
        page.wait_for_timeout(90)
        assert page.evaluate(inside), "Shift+Tab left the clock dialog"
    assert not page.uncaught, page.uncaught


def test_the_face_is_hidden_from_assistive_technology_and_the_value_is_announced(review):
    """**The dial is `aria-hidden` on purpose**, which is why the keyboard mode
    is not optional: there is no honest way to expose 1,440 positions, and a
    screen reader walking twenty-four unlabelled `<text>` nodes is worse than
    silence. What a screen-reader user gets instead is a polite live region
    carrying the value, updated as the hand moves — so this asserts both the
    attribute and that the region's text actually changed on a drag."""
    page = review()
    _open_dial(page)
    assert page.evaluate(
        "document.querySelector('.period-clock-face').getAttribute('aria-hidden')"
    ) == "true"
    live = page.locator("#period-clock-value")
    assert live.get_attribute("aria-live") == "polite"
    before = _announced(page)
    _tap(page, 90)
    after = _announced(page)
    assert after != before and after.startswith("03:"), (
        f"the live region did not follow the hand: {before} -> {after}"
    )
    assert page.evaluate(
        "getComputedStyle(document.querySelector('#period-clock-value')).position"
    ) != "static", "the live region is drawn on screen; it is meant to be read, not seen"
    assert not page.uncaught, page.uncaught


def test_the_backdrop_dismisses_and_a_press_inside_the_dialog_does_not(review):
    """The dialog is a child of the backdrop, so `closest('[data-action]')` from
    anything inside it finds the backdrop. Without the identity test, pressing
    the dialog's own hint closed it — which is the defect the calendar already
    has a note about, one dialog over."""
    page = review()
    _open_dial(page)
    page.click("#period-clock-hint")
    page.wait_for_timeout(200)
    assert page.locator("#period-clock-dialog").count() == 1, (
        "pressing the dialog's own text closed it"
    )
    box = page.locator("#period-clock-dialog").bounding_box()
    page.mouse.click(box["x"] / 2, box["y"] / 2)
    page.wait_for_timeout(250)
    assert page.locator("#period-clock-dialog").count() == 0, "the backdrop did not dismiss"
    assert page.evaluate("document.activeElement?.id") == "period-clock-start"
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# The keyboard mode, which is half the control
# ---------------------------------------------------------------------------


def test_the_number_boxes_carry_labels_bounds_and_a_value_that_reaches_the_field(review):
    """The only keyboard and screen-reader route into the control, so it has to
    be a real form field: a `<label>` pointing at it, `min` and `max` stating the
    range to the spinner and to assistive technology, and a value that arrives in
    the box outside exactly as typed.

    A minute of 7 again, because the one-minute resolution is as much this mode's
    claim as the dial's.
    """
    page = review()
    page.focus("#period-clock-end")
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)
    assert page.evaluate(
        "document.querySelector('label[for=\"period-clock-hour\"]')?.textContent"
    ) == "Hour"
    assert page.evaluate(
        "document.querySelector('label[for=\"period-clock-minute\"]')?.textContent"
    ) == "Minute"
    assert page.locator("#period-clock-hour").get_attribute("max") == "23"
    assert page.locator("#period-clock-minute").get_attribute("max") == "59"

    page.fill("#period-clock-hour", "16")
    page.fill("#period-clock-minute", "7")
    page.wait_for_timeout(150)
    assert _announced(page) == "16:07"
    page.click('[data-action="period-clock-set"]')
    page.wait_for_timeout(250)
    assert page.locator("#period-end-time").input_value() == "16:07"
    assert page.evaluate("document.activeElement?.id") == "period-clock-end", (
        "focus did not return to the button that opened the dialog"
    )
    assert not page.uncaught, page.uncaught


def test_a_number_out_of_range_is_clamped_and_the_box_is_rewritten_when_the_caret_leaves(review):
    """A box that refused the `9` of a `19` about to be typed cannot be typed
    into at all, so the value is clamped in `state` and the box is made to agree
    on blur — the same ruling the four text boxes outside already take, where a
    value that parses is tidied when the caret leaves and a value that does not
    is left alone."""
    page = review()
    page.focus("#period-clock-start")
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)
    page.fill("#period-clock-hour", "99")
    page.wait_for_timeout(150)
    assert _announced(page).startswith("23:"), (
        f"99 was not clamped to 23: {_announced(page)}"
    )
    page.focus("#period-clock-minute")
    page.wait_for_timeout(150)
    assert page.locator("#period-clock-hour").input_value() == "23", (
        "the box still shows 99 while the control holds 23; a field must not show "
        "a value the control does not have"
    )
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# What the clock must not do
# ---------------------------------------------------------------------------


def test_the_clock_does_not_fill_a_date(review):
    """`chooseDay` fills a missing *time* with `00:00`, because a date on its own
    is half a bound and the form would refuse it. The reverse is **not** the
    mirror of that: a date guessed from a time is a day the visitor never stated,
    written into the column this whole feature exists to record honestly.

    So a time set on an empty bound leaves the period half-stated and the
    existing half-interval message says so. That is the right outcome and it is
    the *form's* rule, not the clock's.
    """
    page = review()
    _open_dial(page)
    _tap(page, 270)  # nine o'clock
    _tap(page, 90)   # quarter past
    page.click('[data-action="period-clock-set"]')
    page.wait_for_timeout(250)
    assert page.locator("#period-start-time").input_value() == "09:15"
    assert page.locator("#period-start-date").input_value() == "", (
        "the clock invented a date to go with the time it was given"
    )
    assert "both a date and a time" in page.locator("#period-error").inner_text()
    assert page.locator(CALCULATE).is_disabled()
    assert not page.uncaught, page.uncaught


def test_the_clock_does_not_re_implement_the_24_hour_ceiling(review):
    """**`periodProblem` is the single source of that rule** and the dial does not
    carry a second copy of it.

    Two halves, and both are the point. The face offers every one of the
    twenty-four hours whatever is in the date box — nothing on it is disabled,
    because a bound that lives in two places is a bound that will disagree with
    itself — and the refusal, when it comes, is the *existing* sentence from the
    *existing* check, pointing at the date field.

    The date is two days out, so the interval is past `now + 24h` by at least
    forty-eight hours whatever time of day this runs.
    """
    page = review()
    far = (dt.date.today() + dt.timedelta(days=2)).strftime("%d/%m/%Y")
    page.fill("#period-start-date", far)
    page.wait_for_timeout(150)
    _open_dial(page)
    assert page.evaluate(
        "[...document.querySelectorAll('.period-clock-label')].length"
    ) == 24, "the dial hid hours; the ceiling is not the dial's rule to enforce"
    assert page.evaluate(
        "document.querySelectorAll('.period-clock-face [aria-disabled], "
        ".period-clock-face [disabled]').length"
    ) == 0
    _tap(page, 0)     # midnight is on the inner ring; this is twelve o'clock
    _tap(page, 0)     # on the hour
    page.click('[data-action="period-clock-set"]')
    page.wait_for_timeout(250)
    assert page.locator("#period-start-time").input_value() == "12:00"
    assert "more than 24 hours into the future" in page.locator("#period-error").inner_text(), (
        "the ceiling was not applied by the form's own check: "
        + page.locator("#period-error").inner_text()
    )
    assert page.locator(CALCULATE).is_disabled()
    assert not page.uncaught, page.uncaught


def test_setting_a_time_demotes_a_preset_to_custom(review):
    """Writing a time **is** an edit, exactly as choosing a day is. Somebody who
    pressed *One week* and then set the start to 08:10 did not press a shortcut
    for what is now in the fields — they chose it, and `time_frame` records which
    shortcut was pressed."""
    page = review(time_frame="one_week")
    assert page.locator("#time-frame").input_value() == "one_week"
    _open_dial(page)
    _tap(page, 240)
    _tap(page, 60)
    page.click('[data-action="period-clock-set"]')
    page.wait_for_timeout(250)
    assert page.locator("#period-start-time").input_value() == "08:10"
    assert page.locator("#time-frame").input_value() == "custom", (
        "the preset survived an interval the visitor edited by hand"
    )
    assert page.locator("#period-error").inner_text().strip() == ""
    assert not page.uncaught, page.uncaught


def test_only_one_of_the_two_dialogs_can_ever_be_on_screen(review):
    """Two `aria-modal` dialogs at once is two focus traps fighting over the same
    `Tab`, and a screen reader told twice that the rest of the page is inert.

    **The first attempt at this test was wrong and the browser said so**, which
    is worth writing down: it tried to press the calendar button with the clock
    open and timed out for thirty seconds on *"the backdrop intercepts pointer
    events"*. That is the right answer, not a defect — while one dialog is up the
    other's opener is behind a full-viewport backdrop and the `Tab` cycle is held
    inside — so **neither dialog is reachable from the other by any route a
    visitor has**, which is a stronger guarantee than the one the test was
    reaching for.

    So both are asserted. First the guarantee: the opener is genuinely blocked,
    measured by asking the document what is actually at that point. Then the
    state rule behind it, driven by a direct `.click()` that bypasses hit
    testing — `openClock` and the `period-open` branch each null the other's
    state, and that is the thing that would have to be true if a future layout
    ever put a control outside the backdrop.
    """
    page = review()
    _open_dial(page)
    blocked = page.evaluate(
        """() => {
          const opener = document.getElementById('period-open-start');
          const box = opener.getBoundingClientRect();
          const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
          return hit === opener ? 'reachable' : hit.className;
        }"""
    )
    assert "period-dialog-backdrop" in blocked, (
        f"the calendar's button is reachable with the clock open ({blocked}); a "
        f"modal dialog that does not cover the page is not modal"
    )

    page.evaluate("document.getElementById('period-open-start').click()")
    page.wait_for_selector("#period-dialog", timeout=5000)
    page.wait_for_timeout(200)
    assert page.locator("#period-clock-dialog").count() == 0, (
        "the calendar opened on top of the clock"
    )
    assert page.locator(".period-dialog-backdrop").count() == 1
    page.evaluate("document.getElementById('period-clock-start').click()")
    page.wait_for_selector("#period-clock-dialog", timeout=5000)
    page.wait_for_timeout(200)
    assert page.locator("#period-dialog").count() == 0, (
        "the clock opened on top of the calendar"
    )
    assert page.locator(".period-dialog-backdrop").count() == 1
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# RTL, reduced motion, and a phone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_clock_face_does_not_mirror_under_rtl(review, lang):
    """**A deliberate inconsistency with the calendar, and it is measured rather
    than asserted from the stylesheet.**

    The day grid one field over *does* mirror under `dir="rtl"`, and its own note
    says why: a week is text laid out in reading order, so in Arabic Monday
    belongs on the right and an `ArrowRight` that did not follow it would move
    the focus ring backwards across the screen. A clock is not text. Three
    o'clock is to the right of twelve on every wall in every country that uses
    this dial, and a mirrored face would put 3 where an Arabic reader's own watch
    puts 9.

    Parametrised over both directions and compared position by position, because
    "SVG geometry does not mirror" is exactly the kind of claim that stays true
    until somebody adds an `inset-inline-start` to the stylesheet.
    """
    page = review(lang=lang)
    assert page.evaluate("document.documentElement.dir") == ("rtl" if lang == "ar" else "ltr")
    _open_dial(page)
    labels = _labels(page)
    cx, cy, radius = _face(page)

    assert labels["3"][0] > cx > labels["9"][0], (
        f"{lang}: three o'clock is not to the right of nine: {labels['3']} vs {labels['9']}"
    )
    assert labels["12"][1] < cy < labels["6"][1], (
        f"{lang}: twelve is not above six: {labels['12']} vs {labels['6']}"
    )
    # The inner ring is the same rule at a smaller radius: 15 sits inside 3 on
    # the same side, and 00 sits directly under 12.
    assert cx < labels["15"][0] < labels["3"][0], (
        f"{lang}: the inner ring's 15 is not between the centre and the outer 3"
    )
    assert labels["12"][1] < labels["00"][1] < cy

    # And a pointer press reads the same way round, which is the other half: the
    # arithmetic is in viewport coordinates, which are absolute.
    _tap(page, 90)
    assert _announced(page).startswith("03:"), (
        f"{lang}: a press to the right of the centre did not read as three "
        f"o'clock: {_announced(page)}"
    )
    assert page.evaluate(
        "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    ) == [1278, 1278], f"{lang}: the clock pushed the page sideways"
    assert not page.uncaught, page.uncaught


def test_the_hand_moves_without_a_transition_when_motion_is_reduced(review):
    """`prefers-reduced-motion` is a request, and the hand's easing is the only
    motion this control has. Both sides are measured in one file so that a
    stylesheet change that dropped the transition altogether — which would pass a
    one-sided test — is visible as the *normal* case losing it."""
    reduced = review(reduced_motion="reduce")
    _open_dial(reduced)
    assert reduced.evaluate(
        "getComputedStyle(document.querySelector('.period-clock-hand')).transitionDuration"
    ) == "0s", "the hand still eases when the visitor has asked for less motion"

    normal = review(reduced_motion="no-preference")
    _open_dial(normal)
    assert normal.evaluate(
        "getComputedStyle(document.querySelector('.period-clock-hand')).transitionDuration"
    ) != "0s", (
        "the hand has no transition even without the preference, so the reduced "
        "assertion above proves nothing"
    )
    # And it is dropped again while a drag is in flight, in either case: a hand
    # that eased towards the finger dragging it lags behind it.
    page = normal
    page.mouse.move(*_at(page, 90))
    page.mouse.down()
    page.wait_for_timeout(80)
    assert page.evaluate(
        "getComputedStyle(document.querySelector('.period-clock-hand')).transitionDuration"
    ) == "0s", "the hand eases during a drag, so it lags behind the pointer"
    page.mouse.up()
    assert not normal.uncaught, normal.uncaught


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_whole_clock_fits_inside_the_dialog_on_a_phone(review, lang):
    """**Measured at 390px, because nothing else in the suite would catch it.**

    `min-inline-size: 0` on `.period-grid` is the only thing that stopped the day
    grid measuring 760px inside a 358px dialog, and new markup inherits none of
    that fix. `test_horizontal_overflow.py` walks static pages and never opens a
    dialog, and a dialog's own `overflow: auto` keeps its overflow off the
    document — so the page-level check stays green while the control is unusable
    by touch, which is exactly how the calendar's three missing columns survived
    the life of the feature.

    Both stages are measured, because the minute face and the hour face are
    different numbers of labels at different radii, and the actions row is
    measured because the mode toggle is a whole sentence in twenty catalogues.
    """
    page = review(lang=lang, width=390, height=844)
    _open_dial(page)
    measure = """() => {
      const dialog = document.querySelector('.period-clock-dialog');
      const face = document.querySelector('.period-clock-face');
      const box = dialog.getBoundingClientRect();
      const parts = [...dialog.querySelectorAll('.period-clock-label, .period-clock-stage, .period-clock-mode, .period-clock-set, .period-clock-cancel')];
      return {
        content: Math.round(dialog.clientWidth),
        face: Math.round(face.getBoundingClientRect().width),
        round: Math.round(face.getBoundingClientRect().width - face.getBoundingClientRect().height),
        sideways: Math.round(dialog.scrollWidth - dialog.clientWidth),
        outside: parts.filter(part => {
          const rect = part.getBoundingClientRect();
          return rect.left < box.left - 1 || rect.right > box.right + 1;
        }).map(part => part.textContent.trim()),
        page: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
      };
    }"""
    for stage in ("hours", "minutes"):
        if stage == "minutes":
            _tap(page, 90)
        measured = page.evaluate(measure)
        assert measured["face"] <= measured["content"], (
            f"{lang}/{stage}: the face is {measured['face']}px inside a "
            f"{measured['content']}px dialog, so it does not fit the phone it is "
            f"being read on"
        )
        # **What this guards is the viewBox, not a stylesheet declaration.** An
        # `aspect-ratio: 1` was written on `.period-clock-face` and deleting it
        # survived this test — an `<svg viewBox="0 0 200 200">` at `inline-size:
        # 100%` with an auto block size already has an intrinsic 1:1 ratio, so
        # the declaration decided nothing and has been removed. Measured at both
        # viewports: 260x260 with it and 260x260 without. The same measurement
        # with the viewBox changed to `0 0 200 260` gives 260x338, which this
        # assertion does catch — and a face that is not square is a face where
        # the angle a press means is not the angle it looks like.
        assert measured["round"] == 0, (
            f"{lang}/{stage}: the face is not square ({measured['round']}px out), so "
            f"the angle a press means is not the angle it looks like"
        )
        assert measured["outside"] == [], (
            f"{lang}/{stage}: drawn outside the dialog's own box and so unreachable "
            f"by touch: {measured['outside']}"
        )
        assert measured["sideways"] == 0, (
            f"{lang}/{stage}: the dialog scrolls sideways by {measured['sideways']}px"
        )
        assert measured["page"] == [390, 390], (
            f"{lang}/{stage}: the clock pushed the page sideways: {measured['page']}"
        )
    assert not page.uncaught, page.uncaught
