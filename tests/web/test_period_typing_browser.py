"""The four period boxes punctuating themselves, driven from a real keyboard.

**Why a browser and not the Node harness.** `tests/web/test_period_rules.py`
already runs `maskPeriodText` and `parseTimeText` as functions, over more input
shapes than a page load per case could ever afford. What it cannot run is the
*event*: the mask lives inside `handlePeriodInput`, which reads
`event.inputType` and writes `event.target.value` and `setSelectionRange`, and
all three of the traps this feature had to close are properties of that
handler rather than of the function it calls.

  1. **Backspace must be able to delete a separator.** A mask re-applied on
     every `input` event puts the `/` straight back and the visitor watches a
     keystroke do nothing.
  2. **The caret is restored by counting digits, not characters.** A character
     offset means a different position before and after the rewrite.
  3. **A visitor's own separator is not fought.** `parseDateText` accepts `-`,
     `.`, a space and year-first order, so a mask that forced `dd/mm/yyyy` on
     every keystroke would refuse input shapes this field accepts today.

None of the three is visible from the function; all three are visible from a
keyboard. Hence this file.

**Nothing here may change what is sent.** The values were already correct before
any of this existed — `parseDateText` accepted `1/1/2026` and `parseTimeText`
returned a padded `08:10` for `0810` — so the whole feature is appearance. The
assertion that it stayed appearance is
`test_period_submission_browser.py::test_the_untidy_form_sends_the_same_bytes_
as_the_tidy_one`, which compares two captured request bodies character for
character.

**Running these**::

    docker compose -f docker/compose.yaml build web
    docker compose -f docker/compose.yaml up -d --force-recreate web
    pytest tests/web/test_period_typing_browser.py

`web/` is baked into the image by `docker/web.Dockerfile`, so the build is not
optional, and `up -d` alone has been seen leaving the container on the previous
image — compare `docker inspect --format '{{.Image}}' kaicalc-web` against
`docker inspect --format '{{.Id}}' kaicalc-web:local` before believing a green
run here.
"""

from __future__ import annotations

import os

import pytest

from tests.web.steps import press_continue

pytest.importorskip(
    "playwright.sync_api",
    reason="the mask is an event handler; it is unverified without a real keyboard",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080/index.html")

START_DATE = "#period-start-date"
START_TIME = "#period-start-time"


@pytest.fixture
def review(browser):
    """A page standing on step 5 with *Custom period* chosen and the four boxes
    empty.

    A fixture rather than a helper so every context closes with the test that
    opened it: §6.5 caps a caller at 600 GETs an hour and each page load spends
    one on `/taxonomy`, which is the budget the whole of `tests/web` lives
    inside.
    """
    contexts = []

    def open_page(lang="en"):
        context = browser.new_context(viewport={"width": 1278, "height": 983}, locale="en-NZ")
        contexts.append(context)
        page = context.new_page()
        failures: list[str] = []
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
        page.select_option("#time-frame", "custom")
        page.wait_for_selector(START_DATE, timeout=5000)
        page.wait_for_timeout(150)
        return page

    yield open_page
    for context in contexts:
        context.close()


def _type(page, selector, text, *, clear=False):
    """Real keystrokes, one at a time.

    `press_sequentially` rather than `fill`: `fill` sets the value in one event
    and the whole of this file is about what happens between one digit and the
    next.
    """
    box = page.locator(selector)
    box.click()
    if clear:
        box.press("Control+a")
        box.press("Backspace")
    box.press_sequentially(text, delay=30)
    page.wait_for_timeout(80)


def _caret(page, selector):
    return page.evaluate(f"document.querySelector('{selector}').selectionStart")


def _blur(page, selector):
    page.locator(selector).blur()
    page.wait_for_timeout(120)


# ---------------------------------------------------------------------------
# The separators appear
# ---------------------------------------------------------------------------


def test_the_date_box_inserts_its_own_slashes_as_the_digits_arrive(review):
    """Item 1: `14092026` becomes `14/09/2026` while it is being typed.

    Read at every step rather than only at the end, because *when* the `/`
    appears is the behaviour: a mask that only formatted a complete value would
    leave the box looking wrong for seven of the eight keystrokes, which is
    where a visitor decides whether the field is working.
    """
    page = review()
    box = page.locator(START_DATE)
    box.click()
    seen = []
    for digit in "14092026":
        box.press_sequentially(digit, delay=25)
        seen.append(box.input_value())
    page.wait_for_timeout(100)

    assert seen == ["1", "14", "14/0", "14/09", "14/09/2", "14/09/20", "14/09/202", "14/09/2026"], seen
    assert not page.uncaught, page.uncaught


def test_the_time_box_inserts_its_own_colon_as_the_digits_arrive(review):
    """Item 3, which the owner asked for by analogy with items 1 and 2: `0810`
    becomes `08:10`.

    It is *not* "the end time follows the start time" — no box is ever filled
    from another box's value, and nothing below touches `#period-end-time`.
    """
    page = review()
    box = page.locator(START_TIME)
    box.click()
    seen = []
    for digit in "0810":
        box.press_sequentially(digit, delay=25)
        seen.append(box.input_value())
    page.wait_for_timeout(100)

    assert seen == ["0", "08", "08:1", "08:10"], seen
    assert page.locator(START_DATE).input_value() == "", (
        "typing a time filled a date; no box is ever filled from another box"
    )
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# The three traps
# ---------------------------------------------------------------------------


def test_backspace_deletes_a_separator_instead_of_watching_it_reappear(review):
    """**Trap one.** The mask runs on an insertion and never on a deletion.

    Re-applying it on every `input` event makes the `/` undeletable: the
    visitor removes it, the next event puts it straight back, and the caret
    sits still while the screen does not change — a key that visibly does
    nothing, which is the worst thing a text box can do.

    The separator is deleted from the **middle** deliberately. Deleting the
    last one is not a test: `14/` masks to `14/` either way, because a trailing
    separator at a break point is preserved, so a mask that ran on deletions
    would look innocent there and be caught nowhere.
    """
    page = review()
    _type(page, START_DATE, "14092026")
    assert page.locator(START_DATE).input_value() == "14/09/2026"

    # The caret immediately after the second `/`, so Backspace takes the `/`
    # itself rather than a digit.
    page.evaluate(f"document.querySelector('{START_DATE}').setSelectionRange(6, 6)")
    page.locator(START_DATE).press("Backspace")
    page.wait_for_timeout(120)

    assert page.locator(START_DATE).input_value() == "14/092026", (
        "the separator came back on the same keystroke that deleted it"
    )
    assert not page.uncaught, page.uncaught


def test_the_caret_is_restored_by_counting_digits_and_not_characters(review):
    """**Trap two.** Editing the middle of a masked value.

    The mask may add a character in front of the caret, so a character offset
    means a different position after the rewrite than it did before it.
    Counting the digits before the caret is stable across the rewrite; counting
    characters is off by one separator per separator added, and restoring
    nothing at all leaves the caret at the end of the box, which is where the
    whole of the rest of the value is.

    The value this produces is nonsense — it is a 15th month — and that is
    fine and deliberate: the mask punctuates, the parser judges, and the error
    line says so. What is asserted here is only where the caret went.
    """
    page = review()
    _type(page, START_DATE, "14092026")
    page.evaluate(f"document.querySelector('{START_DATE}').setSelectionRange(1, 1)")
    page.locator(START_DATE).press_sequentially("5", delay=25)
    page.wait_for_timeout(120)

    assert page.locator(START_DATE).input_value() == "15/40/92026"
    assert _caret(page, START_DATE) == 3, (
        f"the caret is at {_caret(page, START_DATE)}; after two digits and the "
        f"separator that follows them it belongs at 3, and at 11 it is at the "
        f"end of a value the visitor was editing the front of"
    )
    assert not page.uncaught, page.uncaught


@pytest.mark.parametrize(
    "typed",
    [
        # Every one of these parses today. `test_period_rules.py::
        # test_the_date_parser_still_accepts_every_shape_it_did` is the list.
        "1/1/2026",
        "14-09-2026",
        "14.09.2026",
        "2026-09-14",
    ],
)
def test_a_visitor_who_types_their_own_separator_is_not_fought(review, typed):
    """**Trap three**, and the one that would be a regression dressed as a
    feature.

    The field accepts `-`, `.`, a space and year-first order, so the mask is off
    the moment the value carries a non-digit it would not itself have placed.
    A mask that reformatted these would refuse input shapes this field accepts
    today — and `2026-09-14` reformatted to `20/26/0914` is not a refusal the
    visitor would even recognise as one.
    """
    page = review()
    _type(page, START_DATE, typed)

    assert page.locator(START_DATE).input_value() == typed, (
        f"the mask rewrote {typed!r}, which parses, into "
        f"{page.locator(START_DATE).input_value()!r}"
    )
    assert not page.uncaught, page.uncaught


def test_an_ime_composition_is_not_reformatted_while_it_is_being_composed(review):
    """**The fourth thing that would break it, and the one no amount of typing
    in English would ever show.**

    An IME composes text in the box between `compositionstart` and
    `compositionend`, and a mask that rewrote `value` in the middle of that
    destroys the composition as it is being made. `inputmode="numeric"` makes it
    unlikely rather than impossible: a physical keyboard with an IME active
    reaches these four boxes like any other control on the page.

    The two events are dispatched rather than produced by a real IME, which is
    the honest limit of this test — what it proves is that the flag is wired to
    the events and consulted by the mask, not that a particular IME behaves. The
    `input` events between them are real keystrokes.
    """
    page = review()
    box = page.locator(START_DATE)
    box.click()
    page.evaluate(
        "document.getElementById('period-start-date').dispatchEvent("
        "new CompositionEvent('compositionstart', { bubbles: true, data: '' }))"
    )
    box.press_sequentially("140", delay=30)
    page.wait_for_timeout(120)
    assert box.input_value() == "140", (
        "the box was re-punctuated in the middle of a composition"
    )

    page.evaluate(
        "document.getElementById('period-start-date').dispatchEvent("
        "new CompositionEvent('compositionend', { bubbles: true, data: '' }))"
    )
    box.press_sequentially("9", delay=30)
    page.wait_for_timeout(120)
    assert box.input_value() == "14/09", (
        "the composition ended and the mask did not come back on"
    )
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# What happens when the caret leaves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("selector", "typed", "tidy"),
    [
        (START_DATE, "1/1/2026", "01/01/2026"),
        (START_DATE, "2026-09-14", "14/09/2026"),
        (START_TIME, "8", "08:00"),
        (START_TIME, "8:5", "08:05"),
        (START_TIME, "8:10", "08:10"),
    ],
)
def test_a_value_that_parses_is_tidied_when_the_caret_leaves(review, selector, typed, tidy):
    """Item 2, and item 3's second half. The trigger is **blur** — "输入光标结束
    的时候" — not a timer and not every keystroke.

    `8` → `08:00` and `8:5` → `08:05` are a widening of `parseTimeText` itself,
    not a second parser used only here: the typed path, the blur path and
    `chooseDay`'s fallback all ask the same function, and two rules for one
    field is how a box comes to show a value the form then refuses.
    """
    page = review()
    _type(page, selector, typed)
    assert page.locator(selector).input_value() == typed, (
        "the value changed before the caret left; blur is the trigger"
    )

    _blur(page, selector)
    assert page.locator(selector).input_value() == tidy
    assert not page.uncaught, page.uncaught


@pytest.mark.parametrize(
    ("selector", "typed"),
    [
        (START_DATE, "31/02/2026"),
        (START_DATE, "14/09/20"),
        (START_DATE, "14-13-2026"),
        (START_TIME, "25:00"),
        (START_TIME, "8:99"),
    ],
)
def test_a_value_that_does_not_parse_is_left_exactly_as_it_was_typed(review, selector, typed):
    """**The other half of the rule, and the more important one.**

    Somebody who wrote a wrong date has to see what they wrote in order to fix
    it. Blanking the box, or rewriting it to something that does parse, throws
    away the only evidence they have of their own mistake — and a value the
    visitor did not choose is worse than a value that is wrong.

    `31/02/2026` is here because only the parser's round-trip check refuses it:
    `new Date(2026, 1, 31)` is happily the 3rd of March.
    """
    page = review()
    _type(page, selector, typed)
    _blur(page, selector)

    assert page.locator(selector).input_value() == typed, (
        "an unparseable value was rewritten on blur"
    )
    assert page.locator("#period-error").is_visible(), (
        "the value was left alone but nothing said why"
    )
    assert not page.uncaught, page.uncaught


def test_a_preset_filled_box_is_not_demoted_by_being_tabbed_through(review):
    """Blur rewrites nothing when there is nothing to rewrite, and that is what
    stops it becoming a second way to demote a preset.

    `demotion()` turns *One week* into `custom` because somebody who edited the
    interval by hand chose those dates. Tabbing past a box is not an edit, and a
    blur handler that committed unconditionally would record `custom` over a
    shortcut nobody stopped pressing. The presets fill the boxes in exactly the
    shape blur would write, so the early return is the whole of the protection —
    which is why it is asserted rather than assumed.
    """
    page = review()
    page.select_option("#time-frame", "one_week")
    page.wait_for_timeout(200)
    before = page.locator(START_DATE).input_value()
    assert before, "the preset filled nothing, so this test proves nothing"

    page.locator(START_DATE).click()
    _blur(page, START_DATE)

    assert page.locator(START_DATE).input_value() == before
    assert page.locator("#time-frame").input_value() == "one_week", (
        "tabbing through an untouched box demoted the preset to `custom`"
    )
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# The button beside the box
# ---------------------------------------------------------------------------


def test_the_calendar_button_draws_an_icon_rather_than_an_empty_rectangle(review):
    """`🗓` (U+1F5D3) **defaults to text presentation**, so Windows drew it
    monochrome out of a symbol font at text weight and it read as an empty box.

    It measures, which is why this is a test and not a screenshot: in the
    page's own font the code point was 16.0px wide — a capital M is 15.6px —
    where an emoji-presentation code point such as `📅` (U+1F4C5) is 22.0px. A
    variation selector would ask for the emoji form and would still be one
    font's decision away from the same rectangle, so the icon is drawn instead.

    §7.6 rule 7 forbids fetching an icon font, so it is inline SVG, and it is
    `aria-hidden` because the button's own `aria-label` is the accessible name
    and two would be read twice.
    """
    page = review()
    button = page.locator("#period-open-start")
    measured = page.evaluate(
        """() => {
          const button = document.getElementById('period-open-start');
          const icon = button.querySelector('svg');
          const box = icon && icon.getBoundingClientRect();
          return {
            text: button.textContent.trim(),
            svg: Boolean(icon),
            hidden: icon && icon.getAttribute('aria-hidden'),
            width: box ? Math.round(box.width) : 0,
            height: box ? Math.round(box.height) : 0,
            label: button.getAttribute('aria-label'),
          };
        }"""
    )

    assert measured["svg"], "the calendar button carries no drawn icon"
    assert "\U0001F5D3" not in measured["text"], (
        "U+1F5D3 is back in the button; it renders as an empty rectangle"
    )
    assert measured["text"] == "", (
        "the button has text content beside its icon, which a screen reader "
        f"would read after the label: {measured['text']!r}"
    )
    assert measured["hidden"] == "true"
    assert measured["label"] == "Choose the start date"
    assert measured["width"] >= 14 and measured["height"] >= 14, (
        f"the icon measures {measured['width']}x{measured['height']}px, which is "
        f"smaller than the code point it replaced"
    )
    assert button.is_visible()
    assert not page.uncaught, page.uncaught
