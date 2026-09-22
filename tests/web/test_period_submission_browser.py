"""What the reporting period does once it has been chosen (contract v1.68).

WP2 built the picker and stopped at `state.periodStart` / `state.periodEnd`.
This file is everything downstream of them: **the request body**, the line the
visitor reads back on the results page, and the body the PDF route is sent.

**The request body is captured, not inferred.** `web/js/submission.js` is one
function returning an object, and a test that called it and looked at the
result would prove that a function returns a string — not that the two instants
reach `POST /api/v1/calculate` in a shape §6.2 accepts. So the POST is
intercepted in a real browser, after a real journey through the wizard, and the
JSON that was actually on the wire is what every assertion below reads.

**One of these tests exists to prove that the typing pass changed nothing.** The four
period boxes now insert their own separators while they are typed and tidy
themselves on blur; `test_the_untidy_form_sends_the_same_bytes_as_the_tidy_one`
drives the same journey twice and compares the two captured bodies character for
character, because "this is appearance only" is a claim about the wire and the
wire is what this file reads.

**Two of these tests exist because of a state the form could reach and the API
refuses.** §6.2's `period_custom_without_interval` makes `time_frame: "custom"`
with two nulls a 422, and the form could produce it two ways: by choosing
*Custom period* and typing nothing, and — less obviously — by pressing *One
week* and then emptying all four boxes, which demotes the answer to `custom`
and leaves no interval behind it. Both are refused in the form now, so the
visitor is told before the request rather than after it.

**Running these**::

    docker compose -f docker/compose.yaml build web
    docker compose -f docker/compose.yaml up -d web
    pytest tests/web/test_period_submission_browser.py

`web/` is baked into the image, so the build is not optional: without it the
browser is served the last image's JavaScript and every assertion below
measures code that is not on disk.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re

import pytest

from tests.web.steps import press_continue

pytest.importorskip(
    "playwright.sync_api",
    reason="the request body is captured from a real browser; the payload is unverified without it",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080/index.html")

CALCULATE = '.step-nav [data-action="calculate"]'

#: §6.2's shape for `period_start` / `period_end`: an ISO-8601 local date-time
#: carrying **no zone and no offset**. Anchored at both ends, because the thing
#: this most has to catch is a `Date` object or an epoch millisecond arriving
#: here instead — `toISOString()` would produce a perfectly well-formed string
#: ending in `Z`, which the validator refuses, and a `Number` is refused
#: outright. Seconds are optional: §6.2 appends them.
WIRE_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$")


@pytest.fixture
def journey(browser):
    """A page standing on step 5 with the calculate POST captured and fulfilled.

    Returns `(page, sent)` where `sent` is a list the intercepted request bodies
    are appended to, in order — `calculate` first, and `export/pdf` after the
    PDF button if it is pressed.

    The POST is fulfilled rather than passed through, for `test_results_export.
    py`'s own reason: what is under test here is the body that left the browser,
    and a real API round trip would make every assertion below depend on the
    stack's seeded factor set as well. `tests/web/test_improvement_submission.
    py` is where the same journey is driven all the way into MySQL.

    A fixture rather than a helper so every context closes with the test that
    opened it: §6.5 caps a caller at 600 GETs an hour and each page load spends
    one on `/taxonomy`.
    """
    contexts = []
    response = json.loads(
        (_fixtures() / "calculate_response.json").read_text(encoding="utf-8")
    )

    def open_page():
        context = browser.new_context(viewport={"width": 1278, "height": 983}, locale="en-NZ")
        contexts.append(context)
        page = context.new_page()
        sent: list[dict] = []
        failures: list[str] = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.uncaught = failures

        def capture(route, body):
            sent.append(json.loads(route.request.post_data))
            route.fulfill(status=200, content_type="application/json", body=body)

        page.route(
            "**/api/v1/calculate*",
            lambda route: capture(route, json.dumps(response)),
        )
        # The PDF route answers bytes, not JSON. Fulfilled with a minimal valid
        # PDF header so `downloadPdf`'s blob handling does not raise; what is
        # asserted is the body that was POSTed, which `capture` has already
        # taken by the time this is answered.
        page.route(
            "**/api/v1/export/pdf*",
            lambda route: (
                sent.append(json.loads(route.request.post_data)),
                route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.7\n"),
            )[-1],
        )
        try:
            page.goto(f"{BASE}?lang=en", wait_until="networkidle", timeout=15000)
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
        return page, sent

    yield open_page
    for context in contexts:
        context.close()


def _fixtures():
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "fixtures"


def _type_shift(page, *, start_date="14/09/2026", start_time="08:10",
                end_date="14/09/2026", end_time="16:20"):
    """The client's own example, typed the way a visitor types it."""
    page.fill("#period-start-date", start_date)
    page.fill("#period-start-time", start_time)
    page.fill("#period-end-date", end_date)
    page.fill("#period-end-time", end_time)
    page.wait_for_timeout(180)


def _type_untidy(page):
    """The same shift, typed the way somebody in a hurry types it.

    `press_sequentially` and not `fill`: a fill is one event carrying the whole
    string, and what is under test is the box punctuating itself between one
    digit and the next and tidying itself when the caret leaves. `blur()` is the
    explicit trigger for the second half — `focusout`, and not a timer.
    """
    for selector, text in (
        ("#period-start-date", "1/1/2026"),
        ("#period-start-time", "0810"),
        ("#period-end-date", "1/1/2026"),
        ("#period-end-time", "1620"),
    ):
        box = page.locator(selector)
        box.click()
        box.press_sequentially(text, delay=30)
        box.blur()
        page.wait_for_timeout(120)
    page.wait_for_timeout(120)


def _values(page):
    return {
        field: page.locator(f"#{field}").input_value()
        for field in ("period-start-date", "period-start-time", "period-end-date", "period-end-time")
    }


def _calculate(page):
    page.click(CALCULATE)
    page.wait_for_selector(".results-page", timeout=20000)


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


def test_a_hand_picked_period_reaches_the_request_as_two_zoneless_instants(journey):
    """**The shift, on the wire.** 08:10 to 16:20 is the requirement the two
    columns exist for, and this is the assertion that it arrives.

    `WIRE_INSTANT` is anchored at both ends deliberately. The failure this
    guards is not a missing field — it is a `Date` object or a
    `toISOString()` call getting between `state.periodStart` and the request:
    the first serialises to a zone-carrying string the validator refuses
    outright, and the second is *worse*, because `…T08:10+13:00` normalised to
    UTC is a well-formed value that says a different time and would be printed
    back to the visitor on their own download.
    """
    page, sent = journey()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    _type_shift(page)
    _calculate(page)

    assert len(sent) == 1, sent
    body = sent[0]
    assert body["time_frame"] == "custom"
    assert body["period_start"] == "2026-09-14T08:10"
    assert body["period_end"] == "2026-09-14T16:20"
    assert WIRE_INSTANT.match(body["period_start"]), body["period_start"]
    assert WIRE_INSTANT.match(body["period_end"]), body["period_end"]
    assert not page.uncaught, page.uncaught


def test_a_preset_sends_the_shortcut_it_was_and_the_interval_it_filled(journey):
    """**v1.67's designed normal case, and the one a reader most expects to be
    a contradiction.** A preset is a button that fills the picker, so
    `one_week` arrives *with* seven days of dates — clause five of
    `ck_submission_period` is "an interval requires a `time_frame`", not "an
    interval requires `custom`", precisely so this passes.

    The span is measured rather than assumed: a template anchored forwards, or
    one that filled the same instant twice, would send two well-formed values
    that describe the wrong week.
    """
    page, sent = journey()
    page.select_option("#time-frame", "one_week")
    page.wait_for_timeout(200)
    _calculate(page)

    body = sent[0]
    assert body["time_frame"] == "one_week", (
        "the preset was collapsed to `custom`; the column records which "
        "shortcut was pressed"
    )
    start = dt.datetime.fromisoformat(body["period_start"])
    end = dt.datetime.fromisoformat(body["period_end"])
    assert end - start == dt.timedelta(days=7), f"{start} -> {end}"
    assert abs(end - dt.datetime.now()) < dt.timedelta(minutes=5), (
        "the template is anchored on now, back to the start of the period"
    )
    assert not page.uncaught, page.uncaught


def test_not_stated_sends_two_nulls_and_not_two_empty_strings(journey):
    """§2.3: absence means "no period was given", and it is `null` rather than
    a sentinel. `""` would be a value the column cannot hold and a `DATETIME`
    MySQL would refuse, and "the field was left out" and "the field was sent
    empty" must not be two different facts."""
    page, sent = journey()
    _calculate(page)

    body = sent[0]
    assert body["time_frame"] is None
    assert body["period_start"] is None
    assert body["period_end"] is None
    assert not page.uncaught, page.uncaught


def test_the_pdf_request_carries_the_same_period_the_calculation_did(journey):
    """§6.2.3. `ExportPayload` inherits `PricingOptions` so that the two routes
    cannot disagree about a period — and that is worth nothing if the front end
    sends it to one of them and not the other. The document has to name the
    period its figures cover, and this route persists nothing, so this body is
    the only way it learns it."""
    page, sent = journey()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    _type_shift(page)
    _calculate(page)

    page.click('[data-action="download-pdf"]')
    page.wait_for_timeout(800)

    assert len(sent) == 2, f"the PDF request was not captured: {sent}"
    calculated, exported = sent
    for field in ("time_frame", "period_start", "period_end"):
        assert exported[field] == calculated[field], (
            f"the download's {field} disagrees with the calculation it documents: "
            f"{exported[field]!r} vs {calculated[field]!r}"
        )
    assert not page.uncaught, page.uncaught


def test_the_untidy_form_sends_the_same_bytes_as_the_tidy_one(journey):
    """**The whole claim of the second pass, measured on the wire.**

    The four boxes punctuate themselves while they are typed and tidy
    themselves when the caret leaves, and *none of that may reach the request*.
    The values were already correct before any of it existed — `parseDateText`
    accepted `1/1/2026` and `parseTimeText` returned a padded `08:10` for
    `0810` — so a visitor who typed the untidy form already submitted the right
    instant and was merely left looking at an untidy box.

    So the same journey is driven twice: once typing `1/1/2026` and `0810` a
    key at a time and letting blur tidy them, once filling the canonical
    strings, and **the whole captured body is compared character for
    character**, not just the two period fields. The whole body because the
    failure this guards is not confined to them: a normalisation that reached
    `time_frame`, or an extra field, or a `Date` object serialised into
    `period_start`, would all be well-formed JSON that said something the
    visitor did not.

    The boxes are read back as well, because "the wire is unchanged" is only
    half the promise; the other half is that the visitor can see what they are
    about to send.
    """
    untidy_page, untidy_sent = journey()
    untidy_page.select_option("#time-frame", "custom")
    untidy_page.wait_for_timeout(150)
    _type_untidy(untidy_page)
    assert _values(untidy_page) == {
        "period-start-date": "01/01/2026",
        "period-start-time": "08:10",
        "period-end-date": "01/01/2026",
        "period-end-time": "16:20",
    }, _values(untidy_page)
    _calculate(untidy_page)

    tidy_page, tidy_sent = journey()
    tidy_page.select_option("#time-frame", "custom")
    tidy_page.wait_for_timeout(150)
    _type_shift(
        tidy_page,
        start_date="01/01/2026",
        start_time="08:10",
        end_date="01/01/2026",
        end_time="16:20",
    )
    _calculate(tidy_page)

    untidy = json.dumps(untidy_sent[0], sort_keys=True, ensure_ascii=False)
    tidy = json.dumps(tidy_sent[0], sort_keys=True, ensure_ascii=False)
    assert untidy == tidy, (
        "typing the untidy form produced a different request body:\n"
        f"  untidy: {untidy}\n  tidy:   {tidy}"
    )
    assert untidy_sent[0]["period_start"] == "2026-01-01T08:10"
    assert untidy_sent[0]["period_end"] == "2026-01-01T16:20"
    assert not untidy_page.uncaught, untidy_page.uncaught
    assert not tidy_page.uncaught, tidy_page.uncaught


# ---------------------------------------------------------------------------
# The two states the API refuses and the form could reach
# ---------------------------------------------------------------------------


def test_custom_with_nothing_typed_cannot_be_sent(journey):
    """§6.2's `period_custom_without_interval`, held in the form.

    Choosing *Custom period* and pressing Calculate sent `time_frame:
    "custom"` beside two nulls, which is a 422 — a refusal the visitor met
    *after* their request instead of a sentence they met before it. It is
    refused rather than quietly turned into "not stated", which is the same
    ruling §6.2 takes: dropping the answer on the visitor's behalf discards a
    selection they made and records a fact they never stated.
    """
    page, sent = journey()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(200)

    error = page.locator("#period-error")
    assert error.is_visible(), "an empty custom period drew no message"
    assert "both a start and an end" in error.inner_text()
    assert page.locator(CALCULATE).is_disabled(), (
        "Calculate is live over a payload the API answers 422 to"
    )
    assert sent == []
    assert not page.uncaught, page.uncaught


def test_emptying_a_preset_by_hand_does_not_leave_custom_with_no_interval(journey):
    """**The same refused state, reached the way nobody looks for.**

    Editing the dates by hand demotes the answer to `custom` — that is WP2's
    only write to `state.timeFrame` and it is right, because somebody who moved
    the dates pressed no shortcut. Empty all four boxes and the demotion has
    already happened: the answer is `custom` and there is no interval left
    under it.

    It is also where the order of two lines decides whether this works.
    `handlePeriodInput` demotes and *then* asks `periodProblem` what is wrong,
    because asking first reads the answer the visitor has just stopped giving —
    `one_week` — and `one_week` with no interval is perfectly legal. One line
    earlier and this test is green on a form that sends a 422.
    """
    page, sent = journey()
    page.select_option("#time-frame", "one_week")
    page.wait_for_timeout(200)
    for field in ("period-start-date", "period-start-time", "period-end-date", "period-end-time"):
        page.fill(f"#{field}", "")
        page.wait_for_timeout(80)
    page.wait_for_timeout(150)

    assert page.locator("#time-frame").input_value() == "custom", (
        "editing the interval by hand did not demote the preset"
    )
    assert page.locator("#period-error").is_visible(), (
        "an emptied preset left `custom` with no interval and said nothing"
    )
    assert page.locator(CALCULATE).is_disabled()
    assert sent == []
    assert not page.uncaught, page.uncaught


# ---------------------------------------------------------------------------
# What the visitor reads back
# ---------------------------------------------------------------------------


def test_the_results_page_reads_a_hand_picked_period_back_as_its_dates(journey):
    """A period that was chosen reads back as what the visitor chose.

    **Never "Custom period".** That is the name of a control; read back to
    somebody looking at their own results it says nothing they did not know
    before they pressed it. What they chose was two instants.

    `dd/mm/yyyy` and not `2026-09-14`: `en-NZ`, in every language, the same pin
    `web/js/stats.js` and `web/js/home.js` carry and the same one the box this
    was typed into carries. A results page that printed it back in a shape that
    field would refuse would be answering O-4 on one screen.
    """
    page, sent = journey()
    page.select_option("#time-frame", "custom")
    page.wait_for_timeout(150)
    _type_shift(page)
    _calculate(page)

    line = page.locator(".results-period").inner_text()
    assert line == "These figures cover: 14/09/2026 08:10 – 14/09/2026 16:20", line
    assert "custom" not in line.lower()
    assert not page.uncaught, page.uncaught


def test_the_results_page_reads_a_preset_back_as_the_shortcut_and_the_dates(journey):
    """**The line that would otherwise tell half the truth.**

    From v1.67 a preset *fills* the interval, so "One week" alone is a claim
    about a period the visitor may since have moved, and the dates alone drop
    the record of which button was pressed. The client's first question of this
    column is *did they mean a standard week, or did they choose those dates?*
    — and only both halves answer it.
    """
    page, sent = journey()
    page.select_option("#time-frame", "one_week")
    page.wait_for_timeout(200)
    _calculate(page)

    line = page.locator(".results-period").inner_text()
    assert line.startswith("These figures cover: One week · "), line
    start = dt.datetime.fromisoformat(sent[0]["period_start"])
    assert start.strftime("%d/%m/%Y %H:%M") in line, (
        f"the line does not carry the interval that was sent: {line!r}"
    )
    assert not page.uncaught, page.uncaught


def test_a_period_that_was_not_stated_draws_no_line_at_all(journey):
    """"Not stated" is step 5's default and the common answer. It renders
    nothing — never the label with nothing after it, and never a phrase
    implying that "not stated" is itself a period."""
    page, sent = journey()
    _calculate(page)

    assert page.locator(".results-period").count() == 0
    assert not page.uncaught, page.uncaught
