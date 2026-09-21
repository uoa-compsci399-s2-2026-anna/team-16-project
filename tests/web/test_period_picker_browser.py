"""Step 5's reporting period, driven from a keyboard in a real browser.

**Every assertion here is a behaviour, not a piece of markup.** "The dialog has
`aria-modal`" is worth almost nothing - the attribute is one line and it is true
of a `<div>` nobody can reach. What is worth asserting is that `Tab` from the
last control does not leave the dialog, that `Esc` puts focus back on the button
that opened it, that an arrow key moves the focused day and leaves exactly one
cell tabbable, and that a day past the 24-hour ceiling cannot be reached by any
of the three routes into it. Those are the claims a screen-reader user and a
keyboard user actually depend on, and they are the ones this repository has a
record of shipping broken behind green markup tests.

**The arrow-key tests double as the re-render proof.** `render()` replaces
`main.innerHTML` on every `setState`, and every arrow key in this component *is*
a `setState` - the cursor lives in `state.periodPicker`. So a dialog held in the
DOM rather than in state would be destroyed by the first `ArrowRight`, and the
roving `tabindex` would be destroyed with it. If those tests pass, the dialog,
the focused day and the single tab stop survived a full re-render, forty-two
cells at a time.

**Running these.** Playwright is not a project dependency - it needs a browser
download a marker installing this package should not be made to take - so the
whole module skips without it::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml build web
    docker compose -f docker/compose.yaml up -d
    pytest tests/web/test_period_picker_browser.py

`web/` is baked into the image, so the build is not optional: without it the
browser is served the last image's JavaScript and every test below measures code
that is not on disk.
"""

from __future__ import annotations

import datetime as dt
import os
import re

import pytest

from tests.web.steps import press_continue

pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the calendar from a keyboard; the dialog is unverified without it",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080/index.html")

#: The four boxes, and the ids the labels point at.
FIELDS = ("period-start-date", "period-start-time", "period-end-date", "period-end-time")

CALCULATE = '.step-nav [data-action="calculate"]'


@pytest.fixture
def review(browser):
    """A page standing on step 5, in a named language.

    A fixture rather than a helper so every context is closed at the end of the
    test that opened it: §6.5 caps a caller at 600 GETs an hour and each page
    load spends one on `/taxonomy`, which is the budget this file has to live
    inside alongside the rest of `tests/web`.
    """
    contexts = []

    def open_page(lang="en", width=1278, height=983):
        # `locale` and `?lang=` both pinned, for `test_step_navigation.py`'s own
        # reason: since v1.25 the calculator negotiates from
        # `navigator.languages`, so a machine whose browser prefers another
        # language renders a page none of the English assertions below match.
        context = browser.new_context(viewport={"width": width, "height": height}, locale="en-NZ")
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
        return page

    yield open_page
    for context in contexts:
        context.close()


def _open_calendar(page, bound="start"):
    page.click(f"#period-open-{bound}")
    page.wait_for_selector("#period-dialog", timeout=5000)
    page.wait_for_timeout(120)


def _focused_day(page):
    return page.evaluate("document.activeElement?.getAttribute('aria-label')")


def _values(page):
    return {field: page.locator(f"#{field}").input_value() for field in FIELDS}


# ---------------------------------------------------------------------------
# Reaching it at all
# ---------------------------------------------------------------------------


def test_custom_reveals_the_fields_and_not_stated_takes_them_away_again(review):
    """The boundary this work package was moved to sit on: a picker nothing can
    reach is a picker whose keyboard behaviour cannot be verified.

    "Not stated" must also *clear* what was typed, not merely hide it. The two
    columns mean "no period was given" by absence (§2.3), so a hidden field
    still holding a date is a value the visitor is about to send having been
    shown that they were not sending one.
    """
    page = review()
    assert page.locator("#period-field").count() == 0, (
        "the period fields are on screen before any period has been stated"
    )

    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    assert page.locator("#period-field").is_visible()
    for field in FIELDS:
        assert page.locator(f"#{field}").is_enabled(), f"#{field} is on screen but cannot be used"

    page.fill("#period-start-date", "14/09/2026")
    page.fill("#period-start-time", "08:10")
    page.select_option("#time-frame", "")
    page.wait_for_timeout(150)
    assert page.locator("#period-field").count() == 0

    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    assert _values(page) == dict.fromkeys(FIELDS, ""), (
        "'Not stated' hid the period without clearing it"
    )
    assert not page.uncaught, page.uncaught


def test_a_preset_fills_the_interval_with_the_period_ending_now(review):
    """v1.67's ruling, driven: the four presets are templates now.

    Two halves, and the second is the one that would be dropped in a rewrite:
    the dates are filled *and* `time_frame` still says `one_week`. An interval
    alone cannot answer "did they mean a standard week, or did they choose those
    dates?", which is the first question the client will ask of this column.

    The clock time is asserted on both bounds because it was wrong: an
    `addDays` that rebuilt the date from (year, month, day) alone anchored every
    start at midnight, so *One week* pressed at 18:41 filled seven days and
    nineteen hours and called it a week.
    """
    page = review()
    page.select_option("#time-frame", "one_week")
    page.wait_for_timeout(200)

    values = _values(page)
    assert page.locator("#time-frame").input_value() == "one_week", (
        "pressing a preset collapsed time_frame to 'custom'; v1.67 keeps the "
        "shortcut's own name"
    )
    start = dt.datetime.strptime(f"{values['period-start-date']} {values['period-start-time']}", "%d/%m/%Y %H:%M")
    end = dt.datetime.strptime(f"{values['period-end-date']} {values['period-end-time']}", "%d/%m/%Y %H:%M")
    assert end - start == dt.timedelta(days=7), f"'One week' filled {end - start}"
    # Anchored on now, backwards. A forward anchor is meaningless against a
    # 24-hour ceiling, and "the week I am reporting on" is the one that ended.
    assert abs((dt.datetime.now().replace(second=0, microsecond=0) - end).total_seconds()) <= 120
    assert page.locator(CALCULATE).is_enabled()
    assert not page.uncaught, page.uncaught


def test_editing_the_interval_by_hand_demotes_a_preset_to_custom(review):
    """*One week* and then three days off the start is not a standard week, and
    `time_frame` may not go on claiming it is. §6.2 records **which shortcut was
    pressed**; somebody who moved the dates pressed none."""
    page = review()
    page.select_option("#time-frame", "one_week")
    page.wait_for_timeout(200)
    page.fill("#period-start-date", "01/09/2026")
    page.wait_for_timeout(150)

    assert page.locator("#time-frame").input_value() == "custom", (
        "the interval was edited by hand and time_frame still names a preset"
    )
    assert not page.uncaught, page.uncaught


def test_a_shift_can_be_typed_without_opening_anything(review):
    """The plan's own sentence: *someone entering 08:10 must not have to open
    anything*. The picker is the convenience; these four boxes are the fast path.

    The compact `0810` is asserted alongside `16:20` because a roster is read as
    four digits and typing a colon is the slow way to enter one.
    """
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "14/09/2026")
    page.fill("#period-start-time", "0810")
    page.fill("#period-end-date", "14/09/2026")
    page.fill("#period-end-time", "16:20")
    page.wait_for_timeout(150)

    assert page.locator("#period-dialog").count() == 0, "typing opened the calendar"
    assert page.locator("#period-error").is_hidden(), page.locator("#period-error").inner_text()
    assert page.locator(CALCULATE).is_enabled()
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------


def test_opening_the_calendar_moves_focus_into_it_and_leaves_one_tab_stop(review):
    """Two claims, and the second is the one a hand-built grid gets wrong.

    Focus has to *move* - a dialog that opens behind the focus ring is a dialog a
    keyboard user has to hunt for. And there must be **one** tab stop into the
    grid, not thirty-one: a roving `tabindex` is the difference between one press
    of Tab and a month of them.
    """
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    _open_calendar(page)

    assert page.evaluate(
        "!!document.getElementById('period-dialog')?.contains(document.activeElement)"
    ), "the calendar opened and focus stayed outside it"
    assert page.locator('.period-grid td[tabindex="0"]').count() == 1, (
        "the grid has more than one tab stop; the roving tabindex is not roving"
    )
    # Every other *enabled* day is explicitly out of the tab order. Counted
    # against the enabled cells rather than against 31, because the days past
    # the 24-hour ceiling carry no `tabindex` at all - so a fixed number here
    # would be a different number in a month whose tail is disabled, and the
    # test would pass or fail on the date it was run.
    offerable = page.locator(".period-grid td[data-day]").count()
    assert offerable >= 14
    assert page.locator('.period-grid td[data-day][tabindex="-1"]').count() == offerable - 1

    # A click inside the dialog must not dismiss it. The dialog is a child of
    # the backdrop that carries the dismiss action, so `closest('[data-action]')`
    # from anything inside it finds the backdrop - without an identity test,
    # clicking the dialog's own hint text closed the calendar.
    page.click("#period-dialog-hint")
    page.wait_for_timeout(150)
    assert page.locator("#period-dialog").count() == 1, (
        "clicking inside the dialog dismissed it"
    )

    # The backdrop itself does dismiss, and focus goes back to the opener.
    page.mouse.click(20, 20)
    page.wait_for_timeout(250)
    assert page.locator("#period-dialog").count() == 0, "the backdrop did not dismiss"
    assert page.evaluate("document.activeElement.id") == "period-open-start"
    assert not page.uncaught, page.uncaught


def test_esc_closes_the_calendar_and_returns_focus_to_the_button_that_opened_it(review):
    """The rule that makes this a dialog rather than a panel. Focus returning to
    `<main>`, or to the top of the form, strands a keyboard user at the wrong end
    of a long step - and `main.js` would do exactly that on its own, because the
    id it restores no longer exists once the dialog is gone."""
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    _open_calendar(page, "end")

    page.keyboard.press("Escape")
    page.wait_for_timeout(250)

    assert page.locator("#period-dialog").count() == 0, "Esc did not dismiss the calendar"
    assert page.evaluate("document.activeElement.id") == "period-open-end", (
        f"focus went to {page.evaluate('document.activeElement.id')!r} rather than "
        "back to the button that opened the calendar"
    )
    assert not page.uncaught, page.uncaught


def test_tab_from_the_last_control_does_not_leave_the_dialog(review):
    """`aria-modal="true"` tells a screen reader the rest of the page is inert.
    It does not stop Tab walking out of the dialog into the form behind it, and a
    visitor who tabs out of a modal has no way of knowing they have."""
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    _open_calendar(page)

    seen = []
    for _ in range(8):
        page.keyboard.press("Tab")
        page.wait_for_timeout(80)
        inside = page.evaluate(
            "!!document.getElementById('period-dialog')?.contains(document.activeElement)"
        )
        seen.append(page.evaluate("document.activeElement.id || document.activeElement.className"))
        assert inside, f"Tab left the dialog after {seen}"

    page.keyboard.press("Shift+Tab")
    page.wait_for_timeout(80)
    assert page.evaluate(
        "!!document.getElementById('period-dialog')?.contains(document.activeElement)"
    ), "Shift+Tab left the dialog"
    assert not page.uncaught, page.uncaught


def test_an_arrow_key_moves_the_focused_day_and_only_one_cell_stays_tabbable(review):
    """**The re-render proof.** Every arrow key is a `setState`, and `setState`
    replaces `main.innerHTML` wholesale - so a dialog, a cursor or a roving
    `tabindex` held in the DOM rather than in `state` would not survive the first
    press. All three do, and focus lands on the day that moved rather than the
    day that was left, which is what the stable `id="period-grid-focus"` buys.
    """
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "10/09/2026")
    page.wait_for_timeout(120)
    _open_calendar(page)

    assert _focused_day(page) == "Thursday, 10 September 2026"
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(150)
    assert page.locator("#period-dialog").count() == 1, "an arrow key destroyed the dialog"
    assert _focused_day(page) == "Friday, 11 September 2026"
    assert page.locator('.period-grid td[tabindex="0"]').count() == 1

    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(150)
    assert _focused_day(page) == "Friday, 18 September 2026"
    page.keyboard.press("ArrowUp")
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(200)
    assert _focused_day(page) == "Thursday, 10 September 2026"
    assert page.locator('.period-grid td[tabindex="0"]').count() == 1
    assert not page.uncaught, page.uncaught


def test_page_keys_move_by_month_and_home_and_end_move_within_the_week(review):
    """The rest of the map. `Home`/`End` are the ends of the **displayed week**
    and not of the month, which is the distinction the two assertions pin."""
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "10/06/2026")
    page.wait_for_timeout(120)
    _open_calendar(page)

    page.keyboard.press("PageUp")
    page.wait_for_timeout(180)
    assert _focused_day(page) == "Sunday, 10 May 2026"
    assert page.locator("#period-dialog-month").inner_text().strip() == "May 2026"

    page.keyboard.press("PageDown")
    page.wait_for_timeout(180)
    assert _focused_day(page) == "Wednesday, 10 June 2026"

    page.keyboard.press("Home")
    page.wait_for_timeout(180)
    assert _focused_day(page) == "Monday, 8 June 2026", "Home is not the start of the week"
    page.keyboard.press("End")
    page.wait_for_timeout(180)
    assert _focused_day(page) == "Sunday, 14 June 2026", "End is not the end of the week"
    assert not page.uncaught, page.uncaught


def test_a_day_is_named_by_its_whole_date_and_the_chosen_one_is_marked_selected(review):
    """"21" read out of a grid of numbers says nothing about which 21st, so every
    cell's accessible name is the whole date. `aria-selected` is what tells a
    screen reader which day the field already holds - the background colour says
    it to everybody else."""
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "10/09/2026")
    page.wait_for_timeout(120)
    _open_calendar(page)

    named = page.locator('.period-grid td[aria-label="Thursday, 10 September 2026"]')
    assert named.count() == 1, "no cell carries the full date as its accessible name"
    assert named.get_attribute("aria-selected") == "true"
    assert page.locator('.period-grid td[aria-selected="true"]').count() == 1, (
        "more than one day is marked selected"
    )
    # Every other enabled day says so explicitly rather than by omission: a cell
    # with no `aria-selected` in a grid that uses it reads as "not applicable".
    assert page.locator('.period-grid td[data-day]:not([aria-selected])').count() == 0
    assert not page.uncaught, page.uncaught


def test_choosing_a_day_fills_the_field_and_returns_focus_to_the_opener(review):
    """Enter chooses. A day with no time beside it would be half a bound and the
    form would refuse it, so midnight is filled in - visibly, in the box next
    door, where it can be typed over."""
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "10/09/2026")
    page.wait_for_timeout(120)
    _open_calendar(page)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(150)
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)

    assert page.locator("#period-dialog").count() == 0
    assert page.locator("#period-start-date").input_value() == "11/09/2026"
    assert page.locator("#period-start-time").input_value() == "00:00"
    assert page.evaluate("document.activeElement.id") == "period-open-start"
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# The rules the form holds
# ---------------------------------------------------------------------------


def test_a_day_past_the_ceiling_is_genuinely_disabled_and_not_merely_grey(review):
    """Three routes into a forbidden day, and all three are closed.

    Greying a cell that a click, a `Tab` or an arrow key still reaches is the
    defect this asserts against: the cell carries no `data-day`, no `tabindex`
    and no `aria-selected`, so a pointer cannot choose it, the roving tabindex
    cannot land on it, and the arrow keys clamp at the boundary rather than
    stepping onto it.
    """
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    _open_calendar(page)

    disabled = page.locator(".period-grid td.is-disabled")
    assert disabled.count() > 0, "no day is disabled; the ceiling is not being drawn"
    assert page.locator(".period-grid td.is-disabled[data-day]").count() == 0, (
        "a disabled day is still a click target"
    )
    assert page.locator(".period-grid td.is-disabled[tabindex]").count() == 0, (
        "a disabled day is still focusable"
    )
    assert disabled.first.get_attribute("aria-disabled") == "true"

    # And the arrow keys cannot walk onto one. Fifteen presses is more than two
    # weeks, which is well past a ceiling that is 24 hours away.
    for _ in range(15):
        page.keyboard.press("ArrowRight")
    page.wait_for_timeout(250)
    reached = _focused_day(page)
    limit = dt.datetime.now() + dt.timedelta(hours=24)
    assert reached is not None
    reached_date = dt.datetime.strptime(re.sub(r"^\w+, ", "", reached), "%d %B %Y")
    assert reached_date.date() <= limit.date(), (
        f"the arrow keys walked past the ceiling to {reached}"
    )
    assert not page.uncaught, page.uncaught


def test_an_end_before_its_start_is_refused_and_calculate_is_blocked(review):
    """Refused means refused: a message a screen reader is told about, and a
    Calculate button that will not fire. Equal ends stay allowed - a zero-length
    period is odd, enters no calculation, and refusing it buys nothing."""
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "14/09/2026")
    page.fill("#period-start-time", "16:20")
    page.fill("#period-end-date", "14/09/2026")
    page.fill("#period-end-time", "08:10")
    page.wait_for_timeout(180)

    error = page.locator("#period-error")
    assert error.is_visible(), "an end before its start drew no message"
    assert error.get_attribute("role") == "alert"
    assert "end before it starts" in error.inner_text()
    assert page.locator(CALCULATE).is_disabled(), "Calculate is live over a refused period"

    page.fill("#period-end-time", "16:20")
    page.wait_for_timeout(180)
    assert error.is_hidden(), "an equal end was refused; equal is allowed"
    assert page.locator(CALCULATE).is_enabled()
    assert not page.uncaught, page.uncaught


@pytest.mark.parametrize(
    ("date", "time", "fragment"),
    [
        # 2030 is years past the visitor's own now + 24 hours. This is the rule
        # the form holds exactly and the API holds loosely, on purpose - see
        # tests/web/test_period_form_bounds.py.
        ("01/01/2030", "09:00", "24 hours"),
        ("31/12/1969", "09:00", "1970"),
        ("31/02/2026", "09:00", "dd/mm/yyyy"),
        ("14/09/2026", "25:00", "24-hour clock"),
    ],
)
def test_the_form_refuses_what_the_contract_refuses(review, date, time, fragment):
    """One case per rule, each named by what the visitor is told.

    `31/02/2026` is in the list because the round-trip check is the only thing
    refusing it: `new Date(2026, 1, 31)` is happily the 3rd of March, so a form
    without that check would store a date nobody typed and print it back to
    them.
    """
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", date)
    page.fill("#period-start-time", time)
    page.fill("#period-end-date", "14/09/2026")
    page.fill("#period-end-time", "16:20")
    page.wait_for_timeout(180)

    error = page.locator("#period-error")
    assert error.is_visible(), f"{date} {time} was accepted"
    assert fragment in error.inner_text(), (
        f"{date} {time} was refused with the wrong reason: {error.inner_text()!r}"
    )
    assert page.locator(CALCULATE).is_disabled()
    assert not page.uncaught, page.uncaught


def test_half_an_interval_is_refused(review):
    """§6.2's `period_half_interval`, held in the form so the visitor hears about
    it here rather than as a 422 after pressing Calculate."""
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "14/09/2026")
    page.fill("#period-start-time", "08:10")
    page.wait_for_timeout(180)

    assert page.locator("#period-error").is_visible()
    assert "both a start and an end" in page.locator("#period-error").inner_text()
    assert page.locator(CALCULATE).is_disabled()
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# Language and direction
# ---------------------------------------------------------------------------


def test_the_month_and_weekday_names_are_en_nz_in_every_language(review):
    """**A decision, not an oversight.** `Intl` would give translated month names
    for free and with no catalogue entries, which is exactly why it is tempting -
    and it would settle O-4 on one screen while `stats.js` and `home.js` go on
    pinning `en-NZ` two pages away. This asserts the pin, so that changing it is
    a decision somebody has to take rather than a default somebody drifts into.
    """
    page = review("de")
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "10/06/2026")
    page.wait_for_timeout(120)
    _open_calendar(page)

    assert page.locator("#period-dialog-month").inner_text().strip() == "June 2026", (
        "the calendar's month name follows the page language; O-4 is open and "
        "the rest of the site pins en-NZ"
    )
    assert _focused_day(page) == "Wednesday, 10 June 2026"
    headings = page.locator(".period-grid th").all_inner_texts()
    assert headings[0].startswith("Mon"), (
        f"the week does not start on Monday: {headings}. The first day follows "
        "from the en-NZ pin, so that it is one decision and not two."
    )
    assert not page.uncaught, page.uncaught


def test_the_grid_mirrors_under_rtl_and_the_arrow_keys_follow_the_screen(review):
    """**Measured, not assumed.** A seven-column grid does not mirror from `dir`
    alone the way a paragraph does - here the table's column order does mirror,
    which is table layout rather than anything in `styles.css`, and that is the
    fact that makes the arrow keys wrong if they are not mirrored with it.

    In Arabic the previous day is drawn to the **right** of the cursor. An
    `ArrowRight` bound to +1 day would move the focus ring leftwards across the
    screen, which is the one thing an arrow key may not do.
    """
    page = review("ar")
    assert page.evaluate("document.documentElement.dir") == "rtl"
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "10/06/2026")
    page.wait_for_timeout(120)
    _open_calendar(page)

    columns = page.evaluate(
        "[...document.querySelectorAll('.period-grid th')].map(th => Math.round(th.getBoundingClientRect().x))"
    )
    assert columns == sorted(columns, reverse=True), (
        f"the grid did not mirror under dir=rtl: {columns}"
    )

    before = page.locator("#period-grid-focus").bounding_box()
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(180)
    after = page.locator("#period-grid-focus").bounding_box()
    assert _focused_day(page) == "Tuesday, 9 June 2026", (
        "ArrowRight did not move to the previous day in a mirrored grid"
    )
    assert after["x"] > before["x"], (
        f"ArrowRight moved the focus ring leftwards: {before['x']} -> {after['x']}"
    )

    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(180)
    back = page.locator("#period-grid-focus").bounding_box()
    assert _focused_day(page) == "Wednesday, 10 June 2026"
    assert back["x"] < after["x"]

    assert page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]") == [1278, 1278], (
        "the dialog pushed the page sideways under dir=rtl"
    )
    assert not page.uncaught, page.uncaught


@pytest.mark.parametrize("lang", ["en", "de", "ar"])
def test_all_seven_columns_fit_inside_the_calendar_on_a_phone(review, lang):
    """**Every day of the week has to be on screen, and one measurement says so.**

    A table box's `min-inline-size` is `auto`, which resolves to its min-content
    width and then wins over the specified width — `table-layout: fixed` does not
    change that. At a 390px viewport the grid computed 760px inside a 358px
    dialog, seven columns of 109px, so a visitor saw Monday to a clipped Thursday
    and reached the rest only by scrolling the dialog sideways. Arrow keys still
    worked, so keyboard use was unaffected and touch use was not.

    **Nothing caught it for the life of the feature.** `test_horizontal_overflow.py`
    walks static pages and never opens the calendar, and the dialog's own
    `overflow: auto` kept the overflow off the document, so the page-level check
    every other screen relies on was satisfied while three columns were
    unreachable.

    Parametrised over three languages because the column headers are the widest
    thing in a cell and a longer word is the obvious way this comes back — though
    the defect itself was never a translation problem: English measured the same
    760px.
    """
    page = review(lang=lang, width=390, height=844)
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(200)
    page.locator('[data-action="period-open"]').first.click()
    page.wait_for_selector(".period-grid")
    page.wait_for_timeout(150)

    measured = page.evaluate(
        """() => {
          const grid = document.querySelector('.period-grid');
          const dialog = grid.closest('.period-dialog');
          const cells = [...grid.querySelectorAll('thead th')];
          const box = dialog.getBoundingClientRect();
          return {
            grid: Math.round(grid.getBoundingClientRect().width),
            content: Math.round(dialog.clientWidth),
            columns: cells.length,
            outside: cells.filter(cell => {
              const rect = cell.getBoundingClientRect();
              return rect.left < box.left - 1 || rect.right > box.right + 1;
            }).length,
            sideways: Math.round(dialog.scrollWidth - dialog.clientWidth),
          };
        }"""
    )

    assert measured["columns"] == 7, measured
    assert measured["grid"] <= measured["content"], (
        f"{lang}: the calendar grid is {measured['grid']}px inside a "
        f"{measured['content']}px dialog, so it does not fit the phone it is "
        f"being read on"
    )
    assert measured["outside"] == 0, (
        f"{lang}: {measured['outside']} of the seven weekday columns are drawn "
        f"outside the dialog's own box — those days cannot be reached by touch"
    )
    assert measured["sideways"] == 0, (
        f"{lang}: the dialog scrolls sideways by {measured['sideways']}px, which "
        f"is how the missing columns were reachable at all and is not a way "
        f"anybody finds them"
    )
