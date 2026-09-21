"""What the Compare Impact button leaves in the database.

**This file asserts against MySQL rather than against the request body, and that
is the whole reason it exists.** `improvement.js`'s `compareImprovement` is the
second caller of `POST /api/v1/calculate`, and it sends its request under the
same `state.token` the Calculate button just used - so §5.3's upsert does not
add a row, it *replaces* the one already there. Two defects lived in that gap
and neither is visible from the outgoing body of the first call:

1. the comparison request carried no ``time_frame``, no ``total_input_kg``, no
   ``total_value_nzd`` and no ``wasted_value_nzd``, so a submission holding
   ``one_year / 50000.000 / 120000.00 / 4500.00`` held four ``NULL``s a moment
   after the visitor pressed a button on the next screen;
2. it converted every destination row with the entry's unit instead of the
   row's own, so half a tonne sent as ``500.000`` by Calculate was rewritten to
   ``0.500`` by Compare.

A test that intercepts the POST would have passed on both: the first request is
correct, and the second one is only wrong *relative* to it. So the journey here
is driven all the way through to the real API, twice, and the row is read back
between the two.

**Running these**::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d
    pytest tests/web/test_improvement_submission.py

The database is not published by ``docker/compose.yaml`` - deliberately, see its
header - so it is read through ``docker exec`` on the compose container, the
same way the project's own runbook reads it. Every prerequisite skips rather
than fails: an unavailable container means this is unverified, not broken.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.web.steps import press_continue


playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the two calls this file measures between",
)

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080/index.html")

#: The compose container name, not the service name. `docker/compose.yaml` names
#: the service `db` and the container `kaicalc-stack-db`; `docker exec` wants the
#: latter.
DB_CONTAINER = os.environ.get("KAICALC_DB_CONTAINER", "kaicalc-stack-db")
DB_USER = os.environ.get("MYSQL_USER", "kaicalc")
DB_PASSWORD = os.environ.get("MYSQL_PASSWORD", "devpass")
DB_NAME = os.environ.get("MYSQL_DATABASE", "kaicalc")


def query(sql: str) -> list[list[str]]:
    """One SQL statement against the running stack's database, as rows of text.

    ``-N -B`` is "no column names, tab separated", which is the one output shape
    that survives a value containing a space without any parsing on this side.
    """
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is not on PATH; the stored submission cannot be read")
    completed = subprocess.run(
        [
            docker, "exec", DB_CONTAINER,
            "mysql", f"-u{DB_USER}", f"-p{DB_PASSWORD}", "-N", "-B", DB_NAME, "-e", sql,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if completed.returncode != 0:
        pytest.skip(f"{DB_CONTAINER} did not answer; the stack is not up: {completed.stderr.strip()}")
    return [line.split("\t") for line in completed.stdout.splitlines() if line.strip()]


def stored_entry(token: str) -> dict[str, str | None]:
    """The submission-level period and the one entry's three optional figures."""
    rows = query(
        "SELECT s.time_frame, e.total_input_kg, e.total_value_nzd, e.wasted_value_nzd "
        "FROM submission s JOIN submission_entry e ON e.submission_id = s.id "
        f"WHERE s.token = '{token}' ORDER BY e.sort_order"
    )
    assert rows, f"no submission was stored under token {token!r}"
    assert len(rows) == 1, f"the journey entered one entry and stored {len(rows)}: {rows}"
    time_frame, total_input, total_value, wasted_value = rows[0]
    none = lambda value: None if value == "NULL" else value  # noqa: E731
    return {
        "time_frame": none(time_frame),
        "total_input_kg": none(total_input),
        "total_value_nzd": none(total_value),
        "wasted_value_nzd": none(wasted_value),
    }


def stored_period(token: str) -> tuple[str | None, str | None]:
    """The submission's two v1.67 instants, as MySQL prints them.

    Read separately from `stored_entry` above because they cannot be compared
    against a constant: from v1.67 a preset is a *template* anchored on now, so
    pressing *One year* stores the year ending at the moment it was pressed.
    What is fixed is the relationship between the two, and that is what the
    tests below assert.
    """
    rows = query(
        "SELECT period_start, period_end FROM submission "
        f"WHERE token = '{token}'"
    )
    assert rows, f"no submission was stored under token {token!r}"
    start, end = rows[0]
    none = lambda value: None if value == "NULL" else value  # noqa: E731
    return none(start), none(end)


def stored_current_kg(token: str) -> list[str]:
    """Every ``current`` line's stored kilograms, sorted, as MySQL prints them."""
    rows = query(
        "SELECT l.qty_kg FROM submission s "
        "JOIN submission_entry e ON e.submission_id = s.id "
        "JOIN submission_line l ON l.submission_entry_id = e.id "
        f"WHERE s.token = '{token}' AND l.scenario = 'current' ORDER BY l.qty_kg"
    )
    return sorted(row[0] for row in rows)


@pytest.fixture
def page(browser):
    """A fresh context, so every run mints its own session token.

    No route interception at all: both calls have to reach the API, because the
    defect is what the *second* one does to the row the *first* one wrote.
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


#: The four figures the journey below types, and what §6.2's columns must hold
#: for them. `total_input_kg` is typed on an entry measured in **tonnes**, so 50
#: is stored as 50,000 kg - the conversion, asserted rather than assumed.
EXPECTED = {
    "time_frame": "one_year",
    "total_input_kg": "50000.000",
    "total_value_nzd": "120000.00",
    "wasted_value_nzd": "4500.00",
}

#: One entry, one tonne, split across two rows measured in **different units**:
#: half a tonne on the first row and 500 kilograms on the second. Both are 500 kg
#: and the API must be told so twice.
EXPECTED_CURRENT_KG = ["500.000", "500.000"]


def calculate(page) -> str:
    """Drive the whole wizard to a real result, and return the session token."""
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    press_continue(page)
    page.wait_for_selector("#total-waste")

    # The unit first: changing it clears the amounts entered against the old one,
    # which is `calculator.js`'s own behaviour and now covers `#total-input` too.
    page.select_option("#total-unit", "tonnes")
    page.wait_for_timeout(80)
    page.fill("#total-waste", "1")
    page.fill("#total-input", "50")
    page.fill("#total-value", "120000")
    page.fill("#wasted-value", "4500")
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')

    # Row one keeps the entry's tonnes; row two is switched to kilograms. This is
    # the pair that tells a per-row conversion from an entry-wide one: 0.5 and 500
    # are the same mass only if each row is converted with its own unit.
    page.fill('[data-line-field="amount"] >> nth=0', "0.5")
    page.wait_for_timeout(80)
    page.select_option('[data-line-field="unit"] >> nth=1', "kilograms")
    page.wait_for_timeout(80)
    page.fill('[data-line-field="amount"] >> nth=1', "500")
    page.wait_for_timeout(120)
    press_continue(page)
    page.wait_for_selector('[data-action="calculate"]')

    page.select_option("#time-frame", "one_year")
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=20000)
    token = page.evaluate("sessionStorage.getItem('kaiCalculatorToken')")
    assert token, "the calculation did not mint a session token, so nothing was stored"
    return token


def allocate(page) -> None:
    """Open the improvement panel and put a valid allocation into it.

    **Why this is a step at all now.** Stage four starts every slider at zero on
    purpose, so the panel opens on a total of 0% and `improvementValidation`
    correctly refuses it - the two scenarios would not describe the same mass.
    A visitor therefore has to allocate before Compare Impact is anything but
    disabled, and so does this file.

    `[data-action="reset-improvement"]` ("Match the current allocation") is the
    visitor-facing shortcut to exactly the allocation these tests used to be
    handed for free, so pressing it reaches the button under test without
    inventing state the interface has no way to produce. It is also the only
    route here that is indifferent to the kilogram/percentage toggle: it sets
    `state.improvedAllocations` - always percentages, in either mode - rather
    than typing into a box whose unit depends on the mode.

    Nothing about the 100%-exactly rule is relaxed to get here. The allocation
    pressed below is one `improvementValidation` accepts on its own terms; if it
    ever stops totalling 100%, the assertion in `compare` fails rather than
    routing around it.
    """
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector('[data-action="reset-improvement"]')
    page.click('[data-action="reset-improvement"]')
    page.wait_for_selector('[data-action="compare-improvement"]')
    page.wait_for_timeout(120)


def compare(page) -> None:
    """Allocate, then press Compare Impact, as a visitor does."""
    allocate(page)
    button = page.locator('[data-action="compare-improvement"]')
    assert button.is_enabled(), (
        "Compare Impact stayed disabled after matching the current allocation - "
        "that allocation does not describe the same mass as the current scenario, "
        "which is defect 2 by another route"
    )
    button.click()
    page.wait_for_selector("#comparison-results", timeout=20000)


def test_calculate_stores_the_four_round_two_figures(page):
    """The precondition, asserted separately so a failure here is not read as a
    failure of the button under test."""
    token = calculate(page)
    assert stored_entry(token) == EXPECTED


def test_compare_impact_does_not_erase_the_four_round_two_figures(page):
    """Defect 1, measured where it happened.

    `compareImprovement` re-sends the whole submission under the same token, so
    a field it omits is a field the visitor loses. It omitted all four.
    """
    token = calculate(page)
    before = stored_entry(token)
    assert before == EXPECTED, before

    compare(page)

    after = stored_entry(token)
    assert after == EXPECTED, (
        f"Compare Impact rewrote the stored submission: {before} -> {after}"
    )


def test_calculate_stores_each_row_in_its_own_unit(page):
    """The other precondition: two rows of equal mass in two units."""
    token = calculate(page)
    assert stored_current_kg(token) == EXPECTED_CURRENT_KG


def test_compare_impact_does_not_rewrite_the_rows_into_the_entrys_unit(page):
    """Defect 2, measured where it happened.

    Half a tonne is 500 kg however it is spelled. Converting it with the entry's
    unit sends `0.500`, and because the token is the same, 500 kg in the
    database becomes half a kilogram.
    """
    token = calculate(page)
    before = stored_current_kg(token)
    assert before == EXPECTED_CURRENT_KG, before

    compare(page)

    after = stored_current_kg(token)
    assert after == EXPECTED_CURRENT_KG, (
        f"Compare Impact rewrote the current scenario's masses: {before} -> {after}"
    )


def test_matching_the_current_allocation_puts_equal_rows_at_equal_shares(page):
    """The same defect where the visitor can see it, before any request is sent.

    **What this used to assert, and why it cannot any more.** The panel seeded
    every slider from `currentAllocationPercentages` the moment it opened, and
    this test read that seeding: two rows of identical mass in different units
    landed at 99.88% and 0.12% instead of 50/50, because the conversion used the
    entry's unit for both rows. Stage four made every slider open at zero
    deliberately - a visitor modelling an improvement is choosing a new
    allocation, and starting from the old one hid which numbers they had
    actually decided - so "opens at equal shares" is not a property this panel
    has any more, for any input, and asserting it would be asserting the absence
    of a change the client asked for.

    **What still has to be true, and is asserted here instead.**
    `currentAllocationPercentages` is unchanged and still live: it is the
    function behind `[data-action="reset-improvement"]` ("Match the current
    allocation"), and it is still the only place the 99.88/0.12 conversion
    defect can return. So this presses that button - the visitor action that now
    reaches the conversion, where merely opening the panel used to - and makes
    the same equal-shares assertion, on the same two-rows-in-two-units fixture,
    against the same controls.

    The per-row "Current: 50.00%" caption is checked as well. It is the same
    function read through a surface that renders in percentage points in *both*
    modes, so it pins the conversion independently of which unit the boxes below
    it happen to be displaying.
    """
    calculate(page)
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector('[data-action="reset-improvement"]')

    captions = page.evaluate(
        """() => [...document.querySelectorAll('.improvement-allocation-row')]
              .map(row => Number((row.querySelector('span')?.textContent || '')
                                 .replace(/[^0-9.]/g, '')))
              .filter(value => value > 0)"""
    )
    assert len(captions) == 2, (
        f"two rows were entered and {len(captions)} carry a current share: {captions}"
    )
    assert all(abs(value - 50) < 0.01 for value in captions), (
        f"two rows of equal mass do not report equal current shares: {captions}"
    )

    page.click('[data-action="reset-improvement"]')
    page.wait_for_timeout(120)
    seeded = page.evaluate(
        """() => [...document.querySelectorAll('.percentage-input [data-improvement-code]')]
              .map(input => ({code: input.dataset.improvementCode, value: Number(input.value)}))
              .filter(row => row.value > 0)"""
    )
    assert len(seeded) == 2, f"two rows were entered and {len(seeded)} were matched: {seeded}"
    assert all(abs(row["value"] - 50) < 0.01 for row in seeded), (
        f"two rows of equal mass were not matched at equal shares: {json.dumps(seeded)}"
    )


def test_matching_the_current_allocation_also_satisfies_the_mass_rule_in_unit_mode(page):
    """The repair above, in the other mode of the unit/percentage toggle.

    `state.improvedAllocations` holds percentages whichever mode is showing, and
    a row's own unit is a display conversion only. That is what lets "Match the
    current allocation" enable Compare Impact in either mode - and it is worth
    an assertion, because storing a mass instead would turn the exactly-100
    rule in `improvementValidation` into a floating-point comparison against a
    mass and start refusing allocations that are correct.

    1,000 kg is allocated across the two rows, so equal shares are 500 kg each -
    every row defaults to kilograms in unit mode, so the boxes read 500, not
    50, and that difference is the evidence the mode really did change rather
    than the label alone.
    """
    calculate(page)
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector("#improvement-mode")
    page.select_option("#improvement-mode", "unit")
    page.wait_for_timeout(120)

    page.click('[data-action="reset-improvement"]')
    page.wait_for_timeout(120)

    shown = page.evaluate(
        """() => [...document.querySelectorAll('.percentage-input [data-improvement-code]')]
              .map(input => Number(input.value))
              .filter(value => value > 0)"""
    )
    assert len(shown) == 2 and all(abs(value - 500) < 0.01 for value in shown), (
        f"unit mode should show the two equal rows as 500 kg each: {shown}"
    )

    button = page.locator('[data-action="compare-improvement"]')
    assert button.is_enabled(), (
        "Compare Impact stayed disabled in unit mode on an allocation that is "
        "valid in percentage mode - the stored allocation is unit-dependent, which "
        "it must not be"
    )


# --------------------------------------------------------- the period (v1.67, v1.68)
#
# The same defect class this whole file exists for, one revision later. `time_frame`
# was one of the four fields `compareImprovement` dropped; v1.67 gave the period two
# more columns beside it, and a builder that sent them on Calculate and not on Compare
# would blank them a moment after the visitor pressed a button on the next screen —
# exactly as before, and again invisible from the first request's own body.
#
# `web/js/submission.js` sends all three through one builder for that reason, and
# these read the row rather than the request.


def test_calculate_stores_the_interval_the_preset_filled(page):
    """v1.67's designed normal case, in the database: `one_year` **and** the
    twelve months it stands for.

    Not compared against a constant, because a template is anchored on now.
    Three facts are, and together they pin it: both columns are written, the
    end is the moment the button was pressed, and the start is the same
    wall-clock instant a year earlier — which is what "the year ending now"
    means and what a forward-anchored or zero-length template would fail.
    """
    token = calculate(page)
    start, end = stored_period(token)
    assert start and end, (
        f"the preset filled no interval: {start!r} -> {end!r}. From v1.67 a "
        "preset is a button that fills the picker, not a word on its own"
    )
    started = dt.datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    ended = dt.datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
    assert started.replace(year=started.year + 1) == ended, f"{started} -> {ended}"
    assert abs(ended - dt.datetime.now()) < dt.timedelta(minutes=10), (
        f"the period does not end at the moment the preset was pressed: {ended}"
    )
    #: The stored instants carry **no zone** (§2.3) and are the visitor's own
    #: wall clock, so `DATETIME` holds them verbatim. Seconds are zero because
    #: the form collects minutes; a value with seconds in it would mean
    #: something between the box and the column invented precision.
    assert started.second == 0 and ended.second == 0


def test_compare_impact_does_not_erase_the_interval(page):
    """Defect 1's shape, applied to v1.67's two new columns.

    `compareImprovement` re-sends the whole submission under the same token, so
    a field it omits is a field the visitor loses — and losing one of these two
    would leave the row in the state `ck_submission_period` exists to forbid:
    `one_year` beside a half-interval, or `custom` beside nothing.
    """
    token = calculate(page)
    before = stored_period(token)
    assert all(before), before

    compare(page)

    after = stored_period(token)
    assert after == before, (
        f"Compare Impact rewrote the stored period: {before} -> {after}"
    )
