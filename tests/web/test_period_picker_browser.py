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
import re

import pytest

from tests.web.base_url import CALCULATOR
from tests.web.steps import press_continue

pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the calendar from a keyboard; the dialog is unverified without it",
)

BASE = CALCULATOR

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
# The two grids behind the caption
# ---------------------------------------------------------------------------


def _open_view(page, view):
    """Press one half of the caption and wait for its grid."""
    page.click(f'[data-action="period-view"][data-view="{view}"]')
    page.wait_for_selector(".period-choices", timeout=5000)
    page.wait_for_timeout(180)


def _cursor(page):
    return page.evaluate("document.getElementById('period-grid-focus')?.textContent")


def test_the_year_grid_crosses_the_whole_range_in_one_press(review):
    """**The defect this package exists for, measured as a count.**

    1970-01-01 to now + 24 hours is fifty-seven years, and the only ways across
    it were 57 `Shift`+`PageUp` presses or 684 clicks on the previous month.
    What is asserted here is that the year grid offers *every* year of the range
    at once - not a decade page a visitor has to walk - and that choosing one
    lands the day grid on the same month of that year.

    The count is derived from the clock rather than written down, so the test
    goes on being about the range instead of about the year it was written in.
    """
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "10/06/2026")
    page.wait_for_timeout(120)
    _open_calendar(page)
    _open_view(page, "years")

    offered = page.locator('td[data-action="period-choose-year"]').all_inner_texts()
    ceiling_year = (dt.datetime.now() + dt.timedelta(hours=24)).year
    assert offered[0] == "1970", f"the year grid does not begin at the floor: {offered[:3]}"
    assert offered[-1] == str(ceiling_year), (
        f"the year grid stops at {offered[-1]} rather than at the ceiling's year"
    )
    assert len(offered) == ceiling_year - 1970 + 1, (
        f"{len(offered)} years are offered where the range holds "
        f"{ceiling_year - 1970 + 1}; a grid that pages is not one press across"
    )
    assert page.locator('.period-choices td[tabindex="0"]').count() == 1, (
        "the year grid has more than one tab stop; the roving tabindex is not roving"
    )

    page.click('td[data-action="period-choose-year"][data-value="1994"]')
    page.wait_for_timeout(250)
    assert page.locator(".period-choices").count() == 0, "choosing a year did not return to the days"
    assert page.locator("#period-dialog-month").inner_text().strip() == "June 1994", (
        "choosing a year changed the month as well as the year"
    )
    assert _focused_day(page) == "Friday, 10 June 1994"
    assert not page.uncaught, page.uncaught


def test_choosing_a_year_states_no_date_and_so_demotes_no_preset(review):
    """**A year is not a date, and `time_frame` records which shortcut was
    pressed.**

    `chooseDay` demotes a preset to `custom` because somebody who moved the
    start back three days did not press a shortcut for what is now in the
    fields. Opening a year grid and choosing 1994 states nothing: no box
    changes, so the answer the visitor gave is still the answer they gave. A
    demotion here would rewrite `time_frame` over a dialog the visitor then
    dismissed without choosing a day at all.
    """
    page = review()
    page.select_option("#time-frame", "one_week")
    page.wait_for_timeout(200)
    before = _values(page)
    assert before["period-start-date"], "the preset did not fill the interval"

    _open_calendar(page)
    _open_view(page, "years")
    page.click('td[data-action="period-choose-year"][data-value="1994"]')
    page.wait_for_timeout(250)
    _open_view(page, "months")
    page.click('td[data-action="period-choose-month"][data-value="0"]')
    page.wait_for_timeout(250)

    assert _values(page) == before, (
        "moving the calendar's cursor wrote into a field; only choosing a day may"
    )
    assert page.locator("#time-frame").input_value() == "one_week", (
        "opening a year grid demoted a preset to custom without a date being stated"
    )
    assert not page.uncaught, page.uncaught


def test_a_year_and_a_month_outside_the_range_are_genuinely_disabled(review):
    """The same four signals the day grid's disabled cells carry, in both new
    grids: no `data-action`, so a pointer cannot choose one; no `tabindex`, so
    the roving cursor cannot land on one; `aria-disabled`, so it is not offered;
    and the colour last rather than only.

    The year grid's last row runs past the ceiling - 2025-2029 for a ceiling in
    2026 - and draws those years disabled rather than leaving them out, so the
    grid's own shape says where the range stops. The ceiling's own year is the
    other boundary: the months after the one the ceiling falls in are out of
    range and are drawn the same way.
    """
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    _open_calendar(page)
    _open_view(page, "years")

    limit = dt.datetime.now() + dt.timedelta(hours=24)
    disabled = page.locator(".period-choices td.is-disabled")
    assert disabled.count() > 0, "no year past the ceiling is drawn; the boundary is invisible"
    assert page.locator(".period-choices td.is-disabled[data-action]").count() == 0, (
        "a disabled year is still a click target"
    )
    assert page.locator(".period-choices td.is-disabled[tabindex]").count() == 0, (
        "a disabled year is still focusable"
    )
    assert disabled.first.get_attribute("aria-disabled") == "true"
    assert all(int(text) > limit.year for text in disabled.all_inner_texts()), (
        f"a year inside the range is disabled: {disabled.all_inner_texts()}"
    )
    assert page.locator(f'td[data-action="period-choose-year"][data-value="{limit.year + 1}"]').count() == 0, (
        "the year after the ceiling's is choosable"
    )
    assert page.locator('td[data-action="period-choose-year"][data-value="1969"]').count() == 0, (
        "1969 is offered; the floor is 1970-01-01"
    )

    # And the clamp: the arrow keys cannot walk onto one either.
    for _ in range(6):
        page.keyboard.press("ArrowRight")
    page.wait_for_timeout(250)
    assert int(_cursor(page)) <= limit.year, f"the arrow keys walked past the ceiling to {_cursor(page)}"

    # The ceiling's own year, in the month grid: the months after it are out.
    _open_view(page, "months")
    assert page.locator("#period-dialog-month").inner_text().strip().endswith(str(limit.year))
    offered = page.locator('td[data-action="period-choose-month"]').count()
    assert offered == limit.month, (
        f"{offered} months are offered in {limit.year}, where the ceiling falls "
        f"in month {limit.month}"
    )
    assert not page.uncaught, page.uncaught


def test_esc_nests_and_each_view_carries_its_own_hint(review):
    """Two rules that are one behaviour: the grids are a stack, and what the
    dialog says about the keyboard has to be about the grid on screen.

    A single `Esc` that closed the whole dialog from a year grid would lose the
    month the visitor had navigated to, for nothing - they asked to leave the
    year grid, not the calendar. And the day grid's hint names `Page Up` and
    `Page Down`, which do nothing in a grid of years: a hint that names keys
    that are not bound is worse than no hint, because it is the only thing
    telling a keyboard user what is bound.
    """
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    _open_calendar(page)
    day_hint = page.locator("#period-dialog-hint").inner_text()
    assert "Page Up" in day_hint, day_hint

    _open_view(page, "years")
    year_hint = page.locator("#period-dialog-hint").inner_text()
    assert "Page Up" not in year_hint, (
        f"the year grid offers the day grid's hint, which names keys it does not "
        f"bind: {year_hint!r}"
    )
    assert "years" in year_hint, year_hint

    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    assert page.locator("#period-dialog").count() == 1, (
        "Esc closed the whole dialog from a year grid rather than returning to the days"
    )
    assert page.locator(".period-choices").count() == 0, "Esc did not leave the year grid"
    assert page.locator(".period-grid th").count() == 7, "the day grid did not come back"
    assert page.evaluate(
        "!!document.getElementById('period-dialog')?.contains(document.activeElement)"
    ), "Esc out of the year grid left focus outside the dialog"

    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    assert page.locator("#period-dialog").count() == 0, "Esc did not close the day grid"
    assert page.evaluate("document.activeElement.id") == "period-open-start"

    # And it opens on the days again - **closed from the year grid**, which is
    # the only close that can tell the rule apart from an accident. A picker that
    # remembered the view would open on the wrong question: the visitor pressed a
    # button labelled "Choose the start date", and what they are shown first has
    # to be the thing that button names. Closing by way of `Esc` would not ask
    # the question, because leaving the year grid is itself a move to the days;
    # measured, with a picker that remembered its last view, and it passed.
    _open_calendar(page)
    _open_view(page, "years")
    page.click('[data-action="period-close"]')
    page.wait_for_timeout(250)
    assert page.locator("#period-dialog").count() == 0, "the close button did not close the dialog"

    _open_calendar(page)
    assert page.locator(".period-choices").count() == 0, (
        "the calendar reopened on the grid it was last left in rather than on the days"
    )
    assert page.locator(".period-grid th").count() == 7
    assert not page.uncaught, page.uncaught


def test_the_year_grid_opens_scrolled_to_the_cursor_and_the_scroll_takes_no_focus(review):
    """Fifty-seven years do not fit a dialog, so the grid scrolls - and a grid
    that opened at 1970 would put the visitor's own year off the bottom of a box
    they have to find the scrollbar of.

    The scroll is applied to the cell that has just been focused, which is what
    keeps it from being a second thing that moves the focus ring: the assertion
    is that the cursor is both focused **and** inside the scroller's own box.
    """
    page = review()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "10/06/2011")
    page.wait_for_timeout(120)
    _open_calendar(page)
    _open_view(page, "years")

    measured = page.evaluate(
        """() => {
          const scroller = document.querySelector('.period-scroller');
          const cell = document.getElementById('period-grid-focus');
          const box = scroller.getBoundingClientRect();
          const seen = cell.getBoundingClientRect();
          return {
            year: cell.textContent,
            focused: document.activeElement === cell,
            scrollTop: Math.round(scroller.scrollTop),
            scrollable: Math.round(scroller.scrollHeight - scroller.clientHeight),
            inside: seen.top >= box.top - 1 && seen.bottom <= box.bottom + 1,
            above: Math.round(seen.top - box.top),
            below: Math.round(box.bottom - seen.bottom),
            row: Math.round(seen.height),
          };
        }"""
    )
    assert measured["year"] == "2011", measured
    assert measured["scrollable"] > 0, (
        "the year grid does not scroll at all, so this test is measuring nothing"
    )
    assert measured["scrollTop"] > 0, (
        "the year grid opened at 1970 with the cursor somewhere below the fold"
    )
    assert measured["inside"], "the cursor is outside the scroller's own box"
    assert measured["focused"], (
        "the cursor is scrolled to but not focused; the scroll and the focus have "
        "come apart"
    )
    # **Centred, and the difference is what this line exists for.** Dropping the
    # scroll and letting `.focus()` do it on its own leaves every assertion above
    # green - measured, scrollTop 249 against 224 - because `.focus()` scrolls to
    # `nearest`, which satisfies "visible" by putting the cursor against an edge
    # with no years on one side of it. 2011 is chosen to sit in the middle of the
    # range, where the scroller is not clamped at either end and centring is
    # therefore something the code either does or does not do.
    assert abs(measured["above"] - measured["below"]) <= measured["row"], (
        f"the cursor is {measured['above']}px from the top of the scroller and "
        f"{measured['below']}px from the bottom, so it was scrolled to an edge "
        f"rather than centred"
    )
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


def test_the_year_and_month_grids_mirror_under_rtl_and_the_arrows_follow_the_screen(review):
    """**Measured in Arabic, not assumed**, for the reason the day grid's own RTL
    test gives: a `<table>`'s column order mirrors with the document, which is
    table layout rather than anything in `styles.css`, and that is exactly the
    fact that makes an unmirrored arrow key wrong.

    The year chosen matters. 2022 sits in the middle of its row - 2020-2024 -
    so one press cannot wrap to another row, and the focus ring's x coordinate
    is therefore a statement about the arrow key rather than about the wrap.
    2020 would have told us nothing: it starts a row, and in *either* direction
    a step from it lands on a different line.
    """
    page = review("ar")
    assert page.evaluate("document.documentElement.dir") == "rtl"
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    page.fill("#period-start-date", "10/06/2022")
    page.wait_for_timeout(120)
    _open_calendar(page)
    _open_view(page, "years")

    row = page.evaluate(
        "[...document.querySelectorAll('.period-choices tr')[0].children].map(c => Math.round(c.getBoundingClientRect().x))"
    )
    assert row == sorted(row, reverse=True), f"the year grid did not mirror under dir=rtl: {row}"

    before = page.locator("#period-grid-focus").bounding_box()
    assert _cursor(page) == "2022"
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(220)
    after = page.locator("#period-grid-focus").bounding_box()
    assert _cursor(page) == "2021", "ArrowRight did not move to the previous year in a mirrored grid"
    assert after["x"] > before["x"], (
        f"ArrowRight moved the focus ring leftwards: {before['x']} -> {after['x']}"
    )
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(220)
    back = page.locator("#period-grid-focus").bounding_box()
    assert _cursor(page) == "2022"
    assert back["x"] < after["x"]

    # The month grid is the same table and the same rule. June is the last cell
    # of its row - April, May, June - so in a mirrored grid it is drawn leftmost
    # and the previous month is the cell to its right.
    _open_view(page, "months")
    months = page.evaluate(
        "[...document.querySelectorAll('.period-choices tr')[0].children].map(c => Math.round(c.getBoundingClientRect().x))"
    )
    assert months == sorted(months, reverse=True), f"the month grid did not mirror: {months}"
    start = page.locator("#period-grid-focus").bounding_box()
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(220)
    moved = page.locator("#period-grid-focus").bounding_box()
    assert page.evaluate(
        "document.getElementById('period-grid-focus').getAttribute('aria-label')"
    ) == "May 2022", "ArrowRight did not move to the previous month in a mirrored grid"
    assert moved["x"] > start["x"], (
        f"ArrowRight moved the focus ring leftwards: {start['x']} -> {moved['x']}"
    )
    assert page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]") == [1278, 1278], (
        "a grid pushed the page sideways under dir=rtl"
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


@pytest.mark.parametrize("lang", ["en", "de", "ml", "ar"])
def test_the_year_and_month_grids_fit_inside_the_calendar_on_a_phone(review, lang):
    """**A new grid is a new table and inherits none of the fix above.**

    `min-inline-size: 0` is what stopped the day grid measuring 760px inside a
    358px dialog, and a `<table>`'s `min-inline-size: auto` resolves to
    min-content and beats `inline-size: 100%` whatever `table-layout` says. The
    year grid is five columns and the month grid is three, both drawn from the
    same class for that reason - and this is the assertion that says so, because
    nothing else would: `test_horizontal_overflow.py` walks static pages and
    never opens a dialog, and the dialog's own `overflow: auto` keeps a grid's
    overflow off the document.

    **German and Malayalam joined the parametrisation when the two grids were
    translated.** The cells are numerals and English month abbreviations in every
    language, so nothing in the grid itself grew - but the hint line above it did,
    and it is the widest thing in the dialog in all twenty catalogues. Measured at
    390px: the hint is 326px in every language here and the dialog it is wrapping
    inside grows taller rather than wider (English months 331px tall, German 353,
    Malayalam 366), which is the outcome this asserts rather than assumes.
    """
    page = review(lang=lang, width=390, height=844)
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(200)
    page.locator('[data-action="period-open"]').first.click()
    page.wait_for_selector(".period-grid")
    page.wait_for_timeout(150)

    measure = """(expected) => {
      const grid = document.querySelector('.period-choices');
      const dialog = grid.closest('.period-dialog');
      const cells = [...grid.querySelector('tr').children];
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
        expected,
      };
    }"""

    for view, columns in (("years", 5), ("months", 3)):
        _open_view(page, view)
        measured = page.evaluate(measure, columns)
        assert measured["columns"] == columns, measured
        assert measured["grid"] <= measured["content"], (
            f"{lang}: the {view} grid is {measured['grid']}px inside a "
            f"{measured['content']}px dialog, so it does not fit the phone it is "
            f"being read on"
        )
        assert measured["outside"] == 0, (
            f"{lang}: {measured['outside']} of the {view} grid's columns are drawn "
            f"outside the dialog's own box - they cannot be reached by touch"
        )
        assert measured["sideways"] == 0, (
            f"{lang}: the dialog scrolls sideways by {measured['sideways']}px with "
            f"the {view} grid open"
        )
    assert not page.uncaught, page.uncaught
