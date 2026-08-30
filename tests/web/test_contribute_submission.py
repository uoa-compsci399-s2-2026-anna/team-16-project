"""What ticking - and not ticking - the contribute control leaves in the database
(contract §6.2.2, item 13).

**This file asserts against MySQL rather than against the request body, and that
is the whole reason it exists.** The strongest evidence for a consent control is
not that a request was sent; it is what the request did to the row `POST
/calculate` already created. `tests/web/test_results_export.py` proves the
control's shape (unticked, labelled, described) and that a request is sent on
the press and not before - but "a request was sent" is not "the flag moved",
and a route fulfilled with a bare 204 tells this suite nothing about the
database on the other side of it. `tests/web/test_improvement_submission.py` is
the precedent for reading that database directly rather than trusting the
network layer to stand in for it, and this file follows it: no route
interception at all, the real API end to end, twice - once through the
checkbox, once without touching it.

**The second half is the one that actually matters.** A test that only proves
ticking works would pass equally against a control that contributes every
calculation regardless of the box - stage one's whole point was that a visitor
who does nothing has answered "no", and that answer has to be provable against
the row, not inferred from the absence of a click.

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

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the calculation this file reads back",
)

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080/index.html")

#: The compose container name, not the service name - see `test_improvement_
#: submission.py`'s identical note.
DB_CONTAINER = os.environ.get("KAICALC_DB_CONTAINER", "kaicalc-stack-db")
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
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "10")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')
    page.fill('[data-line-field="amount"] >> nth=0', "10")
    page.wait_for_timeout(80)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-action="calculate"]')
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=20000)
    token = page.evaluate("sessionStorage.getItem('kaiCalculatorToken')")
    assert token, "the calculation did not mint a session token, so nothing was stored"
    return token


def test_ticking_the_control_moves_the_flag_in_the_database(page):
    """The positive case: the control's whole reason to exist."""
    token = calculate(page)
    assert is_public_contributed(token) is False, (
        "the row is contributed before the visitor was ever asked"
    )

    page.locator("#contribute").check()
    page.wait_for_timeout(600)

    assert is_public_contributed(token) is True, (
        "the box was ticked and the database row was not updated"
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
