"""What the two-step contribute control leaves in the database (contract
§6.2.2, item 13, round four).

**This file asserts against MySQL rather than against the request body, and that
is the whole reason it exists.** The strongest evidence for a consent control is
not that a request was sent; it is what the request did to the row `POST
/calculate` already created. `tests/web/test_results_export.py` proves the
control's shape (unticked, labelled, described), that the tick alone sends
nothing, that Submit arms a five-second window and that Undo cancels it - but "a
request was sent" is not "the flag moved", and a route fulfilled with a bare 204
tells that suite nothing about the database on the other side of it.
`tests/web/test_improvement_submission.py` is the precedent for reading that
database directly rather than trusting the network layer to stand in for it, and
this file follows it: no route interception at all, the real API end to end.

**Round four replaced what this file used to assert, and made it stronger.**
Until then one gesture did everything: ticking the box sent the request, so
"ticking flips the flag in the database" was the positive case. The client then
asked for two steps - a tick on the left, a separate Submit button on the right -
and five seconds after Submit in which the visitor can still take it back. The
old positive assertion is now *false by design*, and the three below replace it
by pulling the single gesture apart into the three outcomes it now has:

* **the tick on its own leaves the flag FALSE.** This is the assertion that
  round four exists for. A control that kept sending on `change` - the whole of
  the previous implementation - passes every shape-level test about a Submit
  button merely existing beside it, and fails here.
* **Submit plus its five seconds flips it TRUE.** Without this the file would
  prove only that nothing ever happens, which a control wired to nothing at all
  would also satisfy.
* **Undo leaves it FALSE**, checked well past the end of the window it cancelled.
  The five seconds are spent *before* the request precisely because §6.2.2 has no
  path that clears the flag once set (`set_public_contribution` only ever sets it
  TRUE), so "the visitor undid it" and "the row was never flagged" have to be the
  same state in the database. This is the one assertion that would catch an
  implementation that sent first and tried to be clever afterwards.

**And the negative case is still here.** A test that only proves the positive
case would pass equally against a control that contributes every calculation
regardless of the box - stage one's whole point was that a visitor who does
nothing has answered "no", and that answer has to be provable against the row,
not inferred from the absence of a click.

**Why the assertions are on the row and the waits are generous.** `is_public_
contributed` is read back through `docker exec`, so each read costs a process;
the waits below are the grace window plus headroom rather than a poll. The two
assertions that a flag has NOT moved are the ones that could go vacuous on a
short wait, so both of them wait longer than the window they are testing, not
shorter.

**Running these**::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d
    pytest tests/web/test_contribute_submission.py

The database is not published by ``docker/compose.yaml`` - deliberately - so it
is read through ``docker exec`` on the compose container, the same way
`test_improvement_submission.py` and the project's own runbook read it. Every
prerequisite skips rather than fails: an unavailable container means this is
unverified, not broken.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.web.base_url import (
    CALCULATOR,
    compose_container,
    compose_stack_mismatch,
)
from tests.web.steps import press_continue


playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the calculation this file reads back",
)

ROOT = Path(__file__).resolve().parents[2]
BASE = CALCULATOR

#: v1.103. The calculation is submitted over HTTP to `BASE` and the row
#: is read back out of a container. On two different stacks the row is
#: written to one database and looked for in the other, which fails as a
#: flag that did not move rather than as what it is.
_SPLIT = compose_stack_mismatch()
if _SPLIT:  # pragma: no cover - environment guard
    pytest.skip(_SPLIT, allow_module_level=True)

#: The compose container name, not the service name - see `test_improvement_
#: submission.py`'s identical note.
#: v1.103. **Resolved from the compose project, not written down.**
#: `docker/compose.yaml` names the service `db` and pins its container
#: `kaicalc-stack-db`, and `docker exec` wants the latter - but a pinned
#: container name does not follow `COMPOSE_PROJECT_NAME` and `docker compose
#: ps` does. With the literal, a run pointed at a private stack by
#: `KAICALC_WEB_URL` submitted the calculation to that stack and looked for the
#: row in the development one; it cost four cases here, reported as a flag that
#: had not moved. `KAICALC_DB_CONTAINER` still overrides, and the literal is
#: the fallback for a checkout with no docker.
DB_CONTAINER = (os.environ.get("KAICALC_DB_CONTAINER")
                or compose_container("db", "kaicalc-stack-db"))
DB_USER = os.environ.get("MYSQL_USER", "kaicalc")
DB_PASSWORD = os.environ.get("MYSQL_PASSWORD", "devpass")
DB_NAME = os.environ.get("MYSQL_DATABASE", "kaicalc")


def query(sql: str) -> list[list[str]]:
    """One SQL statement against the running stack's database, as rows of text."""
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


def is_public_contributed(token: str) -> bool:
    rows = query(f"SELECT is_public_contributed FROM submission WHERE token = '{token}'")
    assert rows, f"no submission was stored under token {token!r}"
    assert len(rows) == 1, f"one token named {len(rows)} rows: {rows}"
    return rows[0][0] == "1"


@pytest.fixture
def page(browser):
    """A fresh context and a fresh token, with no route interception at all - the
    real `/calculate` and the real `/contribute`, because what is under test is
    what the second one leaves in the row the first one wrote."""
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


def calculate(page) -> str:
    """Drive the wizard to a real result, and return the session token."""
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    press_continue(page)
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "10")
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    page.fill('[data-line-field="amount"] >> nth=0', "10")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('[data-action="calculate"]')
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=20000)
    token = page.evaluate("sessionStorage.getItem('kaiCalculatorToken')")
    assert token, "the calculation did not mint a session token, so nothing was stored"
    return token


#: Round four's grace window, in milliseconds, plus the headroom every wait here
#: takes over it. Kept in step with `CONTRIBUTE_GRACE_MS` (`web/js/results.js`).
GRACE_MS = 5000
SLACK_MS = 1500


def test_ticking_the_control_on_its_own_leaves_the_flag_alone(page):
    """**Round four's own assertion.** The tick is a statement of intent; the
    Submit button beside it is what sends. So a box that is ticked and left alone
    has to leave the row exactly as `POST /calculate` wrote it.

    The wait is longer than any request this stack makes, so "the flag has not
    moved yet" cannot pass for "the flag will never move" - and considerably
    longer than the five-second window a press would have opened, so a version
    that armed the timer on the tick rather than on Submit would have fired by
    the time this reads the row.
    """
    token = calculate(page)
    assert is_public_contributed(token) is False, (
        "the row is contributed before the visitor was ever asked"
    )

    page.locator("#contribute").check()
    page.wait_for_timeout(GRACE_MS + SLACK_MS)

    assert is_public_contributed(token) is False, (
        "ticking the box on its own contributed the calculation - the visitor has "
        "not pressed Submit and has been given no chance to undo"
    )


def test_submit_and_its_five_seconds_move_the_flag_in_the_database(page):
    """The positive case: the control's whole reason to exist, now reached in two
    steps rather than one.

    The row is read once *during* the window as well as after it. That middle
    read is what separates "the request goes when the window ends" from "the
    request goes on the press and the countdown is decoration" - the second would
    put the row in the public aggregate while the page was still offering an Undo
    button, which is the precise failure the grace period is built to avoid.
    """
    token = calculate(page)
    assert is_public_contributed(token) is False, (
        "the row is contributed before the visitor was ever asked"
    )

    page.locator("#contribute").check()
    page.locator("#contribute-action").click()
    page.wait_for_timeout(1200)

    assert is_public_contributed(token) is False, (
        "the row was flagged while the undo window was still open, so the Undo "
        "button on screen was offering to withdraw something already sent"
    )

    page.wait_for_timeout(GRACE_MS + SLACK_MS)

    assert is_public_contributed(token) is True, (
        "Submit was pressed and its five seconds ran out, and the database row "
        "was not updated"
    )


def test_undoing_inside_the_window_leaves_the_calculation_out_of_the_public_count(page):
    """**The assertion the whole design turns on.** `set_public_contribution`
    (`db/repository.py`) only ever sets `is_public_contributed` to TRUE and
    §6.2.2 has no route that clears it, so an undo implemented after the request
    would be undoable in the interface and permanent in the database. It is
    implemented as a grace period before the request instead: Undo cancels a
    `setTimeout`, and nothing needs unsending because nothing was sent.

    Which means the evidence for "undone" is not a second request - it is the
    absence of the first one, measured on the row itself, long after the window
    the press opened would have closed. A timer that Undo failed to cancel - the
    obvious defect, since `render()` replaces `main.innerHTML` on every state
    change and a handle stored inside it would be unreachable by the time Undo
    was pressed - fires at five seconds and is caught here.
    """
    token = calculate(page)

    page.locator("#contribute").check()
    page.locator("#contribute-action").click()
    page.wait_for_timeout(600)
    #: The same slot, now carrying Undo - see `contributeBlock`'s note on why one
    #: id is worn by two buttons in turn.
    page.locator("#contribute-action").click()

    page.wait_for_timeout(GRACE_MS + SLACK_MS)

    assert is_public_contributed(token) is False, (
        "the calculation was contributed anyway after the visitor pressed Undo "
        "inside the five seconds they were promised"
    )


def test_leaving_it_unticked_leaves_the_calculation_out_of_the_public_count(page):
    """The negative case, and the one that actually matters (per review). A test
    that only proves the positive case would pass equally against a control
    wired to contribute every calculation regardless of the box - stage one's
    whole point is that doing nothing is a real "no", provable on the row
    itself rather than inferred from no network call having been observed."""
    token = calculate(page)

    #: A visitor who reads the page and closes it. Nothing is clicked here -
    #: the assertion is on what that produces in the row, not on the absence
    #: of a request this test happened to be watching for.
    page.wait_for_timeout(600)

    assert is_public_contributed(token) is False, (
        "a calculation the visitor never offered is already in the public count"
    )
