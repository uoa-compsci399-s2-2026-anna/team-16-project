"""The import dialog, driven in a real browser against the running stack.

**What the markup cannot tell you.** `tests/admin/test_import_dialog.py`
asserts that the drop target is in the page and that the script binds
`dragenter`, `dragover` and `drop`. None of that is evidence that dropping a
file does anything: a `drop` handler whose `dragover` is not cancelled is, by
every markup-only measure, "wired" — and in a browser it does nothing at all,
because the browser has already navigated to the file. That failure is the
single most common way a hand-written drop zone is shipped broken, and it is
invisible to every server-side test in this repository.

The same applies, harder, to the preview. Upsert makes a mistyped `code` a
**new row** instead of a correction, so the preview is the only place a reader
catches one — and "the confirm button is rendered with `d-none` on it" is not
the same claim as "nothing is written until it is pressed". This file presses
nothing and asks the database; then presses it and asks again.

Requires the stack rebuilt:

    docker compose -f docker/compose.yaml build admin
    docker compose -f docker/compose.yaml up -d admin

Skipped, never failed, when the stack is not up — the suite has to stay
runnable on a checkout.

**Every row this file creates is removed again, before and after.** A stray
`sector` row is not litter in this database: it is a stage of the supply chain
that appears in the public calculator. The account is a throwaway created and
deleted here; `admin`, `admin2` and `test123` are never touched.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request

import pyotp
import pytest

pytestmark = pytest.mark.browser

BASE = "http://localhost:18080"
COMPOSE = ["docker", "compose", "-f", "docker/compose.yaml"]

_USERNAME = "kctest_import_dialog"
_DISPLAY_NAME = "Import dialog browser test"
_NEW_PASSWORD = "a-throwaway-password-not-reused-anywhere-else"

#: Every `sector.code` this file writes starts with this, and the sweep below
#: deletes on the prefix rather than on a hand-kept list — the same discipline
#: as `wp1_` through `wp4_` in the server-side files, and it matters more here
#: because this is the deployment's own database rather than a test one.
_PREFIX = "kctest_imp_"

_COLUMNS = ["code", "name", "description", "sort_order", "active"]


def _stack_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

if not _stack_is_up():  # pragma: no cover - environment guard
    pytest.skip(
        f"the stack is not answering on {BASE}; run "
        "`docker compose -f docker/compose.yaml up -d --build admin`",
        allow_module_level=True,
    )


def _docker_exec(*args: str, input_text: str | None = None):
    return subprocess.run(
        [*COMPOSE, "exec", "-T", "admin", *args],
        input=input_text, capture_output=True, text=True, timeout=90,
    )


def _sql(statements: str) -> str:
    """Run SQL inside the panel's own container and print the result.

    `db/repository.py` is the only module of the *application* that touches
    the database; this is a test's teardown reaching into a deployment, the
    same way `tests/admin/test_button_hint_browser.py` removes its own
    account.
    """
    script = (
        "import os, json\n"
        "from sqlalchemy import create_engine, text\n"
        "engine = create_engine(os.environ['DATABASE_URL'])\n"
        "with engine.begin() as conn:\n"
        + statements
    )
    result = _docker_exec("python", input_text=script)
    assert result.returncode == 0, (
        f"the SQL helper failed: {result.stdout}\n{result.stderr}"
    )
    return result.stdout


def _sectors() -> dict:
    """`{code: name}` for every sector this file has written."""
    out = _sql(
        "    rows = conn.execute(text(\"SELECT code, name FROM sector WHERE "
        "code LIKE :p\"), {'p': %r}).all()\n"
        "    print(json.dumps({r[0]: r[1] for r in rows}))\n" % (_PREFIX + "%",)
    )
    return json.loads(out.strip().splitlines()[-1])


def _sweep_sectors() -> None:
    _sql(
        "    ids = conn.execute(text(\"SELECT id FROM sector WHERE code LIKE "
        ":p\"), {'p': %r}).scalars().all()\n"
        "    for i in ids:\n"
        "        conn.execute(text(\"DELETE FROM audit_log WHERE table_name = "
        "'sector' AND row_id = :i\"), {'i': i})\n"
        "    conn.execute(text(\"DELETE FROM sector WHERE code LIKE :p\"), "
        "{'p': %r})\n" % (_PREFIX + "%", _PREFIX + "%")
    )


def _create_throwaway_account() -> str:
    result = _docker_exec(
        "sh", "-c",
        'export SECRET_KEY=$(cat /var/lib/kaicalc/secret_key) && '
        f'python -m admin.cli create-staff {_USERNAME} "{_DISPLAY_NAME}" --admin',
    )
    assert result.returncode == 0, (
        "could not create the throwaway account (is one left over from an "
        f"interrupted run?): {result.stdout}\n{result.stderr}"
    )
    match = re.search(r"Initial password: (\S+)", result.stdout)
    assert match, f"create-staff did not print an initial password: {result.stdout}"
    return match.group(1)


def _delete_throwaway_account() -> None:
    _sql(
        "    conn.execute(text(\"DELETE FROM audit_log WHERE actor = :u\"), "
        "{'u': %r})\n"
        "    conn.execute(text(\"DELETE FROM staff WHERE username = :u\"), "
        "{'u': %r})\n" % (_USERNAME, _USERNAME)
    )


def _log_in(page, password: str) -> None:
    """The real first-login flow: password, forced change, TOTP enrolment,
    recovery codes. There is no shortcut through it from outside the process
    the stack runs in."""
    page.goto(f"{BASE}/admin/login", wait_until="networkidle")
    page.fill('input[name="username"]', _USERNAME)
    page.fill('input[name="password"]', password)
    page.click('button:has-text("Continue")')
    page.wait_for_load_state("networkidle")

    assert "/admin/change-password" in page.url, page.url
    page.fill("#password", _NEW_PASSWORD)
    page.fill("#confirm", _NEW_PASSWORD)
    page.click('button:has-text("Set password")')
    page.wait_for_load_state("networkidle")

    assert "/admin/enrol" in page.url, page.url
    secret = page.inner_text("code.key").replace(" ", "").strip()
    page.fill("#code", pyotp.TOTP(secret).now())
    page.click('button:has-text("Confirm")')
    page.wait_for_load_state("networkidle")

    page.click('a:has-text("I have saved them")')
    page.wait_for_load_state("networkidle")
    assert page.url.rstrip("/") == f"{BASE}/admin", page.url


# --- Playwright, MODULE-scoped ---------------------------------------------
#
# `scope="module"`, never `scope="package"`, for the reason
# tests/admin/test_button_hint_browser.py sets out at length: Playwright's
# sync API keeps an asyncio loop running on this OS thread for as long as its
# context is open, and every pytest-asyncio test collected after it in the
# same package then fails at fixture setup.


@pytest.fixture(scope="module")
def _playwright():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser(_playwright):
    instance = _playwright.chromium.launch()
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def _dialog_account():
    password = _create_throwaway_account()
    yield password
    _delete_throwaway_account()


@pytest.fixture(scope="module")
def authed_storage_state(browser, _dialog_account):
    context = browser.new_context()
    page = context.new_page()
    _log_in(page, _dialog_account)
    state = context.storage_state()
    context.close()
    return state


@pytest.fixture(autouse=True)
def _clean_sectors():
    """Before and after. A run interrupted partway through would otherwise
    leave a stage of the supply chain in the public calculator."""
    _sweep_sectors()
    yield
    _sweep_sectors()


@pytest.fixture
def dialog(browser, authed_storage_state):
    """The Sectors list with the import dialog open."""
    context = browser.new_context(storage_state=authed_storage_state)
    page = context.new_page()
    page.goto(f"{BASE}/admin/sector/list", wait_until="networkidle")
    page.click("#action-import")
    page.wait_for_selector("#kaicalc-import-drop", state="visible")
    yield page
    context.close()


def _drop(page, name: str, body: str, mime: str) -> None:
    """Drop a file on the zone the way a person would, through a real
    `DragEvent` carrying a real `DataTransfer`.

    Not `set_input_files`. That drives the `<input type="file">` directly and
    would pass against a dialog with no drop handling at all — which is
    precisely the state sqladmin ships in, and precisely what this file exists
    to tell apart.
    """
    page.evaluate(
        """([name, body, mime]) => {
            const dt = new DataTransfer();
            dt.items.add(new File([body], name, { type: mime }));
            const zone = document.getElementById('kaicalc-import-drop');
            zone.dispatchEvent(new DragEvent('dragenter',
                { dataTransfer: dt, bubbles: true, cancelable: true }));
            zone.dispatchEvent(new DragEvent('dragover',
                { dataTransfer: dt, bubbles: true, cancelable: true }));
            zone.dispatchEvent(new DragEvent('drop',
                { dataTransfer: dt, bubbles: true, cancelable: true }));
        }""",
        [name, body, mime],
    )


def _csv(code: str, name: str) -> str:
    return ",".join(_COLUMNS) + f"\r\n{_PREFIX}{code},{name},,951,1\r\n"


def _json(code: str, name: str) -> str:
    return json.dumps([{
        "code": f"{_PREFIX}{code}", "name": name, "description": None,
        "sort_order": "952", "active": "true",
    }])


# --- 1. a dropped file is the file the dialog will send --------------------


def test_dropping_a_file_on_the_zone_chooses_it(dialog):
    """The whole of gap 4, measured rather than inferred.

    Two assertions and they are not the same one. The visible name is what a
    person sees; `input.files.length` is what will actually be uploaded. A
    zone that only updated the label would show the right filename and post
    nothing.
    """
    _drop(dialog, "sectors.csv", _csv("drop", "Dropped"), "text/csv")

    assert dialog.inner_text("#kaicalc-import-filename").strip() == "sectors.csv", (
        "dropping a file on the zone did not choose it; the dialog still says "
        f"{dialog.inner_text('#kaicalc-import-filename')!r}"
    )
    assert dialog.evaluate(
        "() => document.getElementById('kaicalc-import-file').files.length"
    ) == 1, (
        "the dialog shows a filename but its file control is empty, so the "
        "upload would carry no file"
    )


def test_the_browser_does_not_navigate_away_to_a_dropped_file(dialog):
    """`dragover` cancelled, driven rather than grepped for.

    Uncancelled, the browser's own default for a dropped file is to open it —
    replacing the panel with the raw text of a CSV, with whatever was typed
    into the dialog gone. The event's `defaultPrevented` is the exact thing
    that decides it.
    """
    prevented = dialog.evaluate(
        """() => {
            const dt = new DataTransfer();
            dt.items.add(new File(['a'], 'x.csv', { type: 'text/csv' }));
            const zone = document.getElementById('kaicalc-import-drop');
            const over = new DragEvent('dragover',
                { dataTransfer: dt, bubbles: true, cancelable: true });
            zone.dispatchEvent(over);
            return over.defaultPrevented;
        }"""
    )
    assert prevented is True, (
        "the dialog does not cancel `dragover`, so the browser will navigate "
        "to a dropped file instead of handing it to this dialog — the most "
        "common way a drop zone is shipped doing nothing at all"
    )


# --- 2. the preview, and the confirmation that is not pre-given ------------


def test_checking_a_file_previews_it_and_writes_nothing(dialog):
    """Pressed once, and the database asked.

    Not "the dry-run route returns a plan" — tests/admin/test_import_modes.py
    already has that. This is the browser half: that the dialog's first press
    is the dry run, that what comes back is drawn, and that the table is
    untouched afterwards.
    """
    assert _sectors() == {}, "the sweep left rows behind"

    _drop(dialog, "sectors.csv", _csv("preview", "Previewed"), "text/csv")
    dialog.click("#kaicalc-import-check")

    # WAIT FOR *EITHER* OUTCOME, AND ASK THE DATABASE FIRST. Written as
    # `wait_for_selector("#kaicalc-import-preview")` this test went red under a
    # mutation that made the first press a real import — but it went red on a
    # 30-second Playwright timeout, because a real import answers a progress
    # stream that the preview cannot be drawn from, so the assertion that
    # actually matters was never reached. A test that fails for the wrong
    # reason is a test whose message teaches the next reader nothing.
    # `:visible` ON EACH ALTERNATIVE, AND THAT WAS MEASURED. Written as
    # `"#kaicalc-import-preview, #kaicalc-import-message"` this waits on
    # whichever element comes FIRST IN THE DOM — `querySelector` semantics —
    # so it watches the preview and never notices the message, and the
    # mutation above timed out instead of reaching the assertion below.
    dialog.wait_for_selector(
        "#kaicalc-import-preview:visible, #kaicalc-import-message:visible",
        timeout=15000,
    )
    assert _sectors() == {}, (
        "the dialog's first press wrote the file. It is supposed to ask what "
        "the file would do (`X-Dry-Run: true`) and draw the answer — the same "
        "code path with the write left off"
    )

    preview = dialog.inner_text("#kaicalc-import-preview")
    assert "1 row(s) would be added" in preview, (
        f"the preview does not say what the file would do: {preview!r}"
    )
    assert f"{_PREFIX}preview" in preview, (
        "the preview does not name the row it would add. On an upsert a row "
        '"to be added" is either one somebody meant to add or a key with a '
        f"typo in it, and a count cannot tell those apart: {preview!r}"
    )
    assert _sectors() == {}, (
        "checking the file wrote it; the preview is supposed to be the same "
        "code path with the write left off"
    )


def test_the_preview_stacks_its_summary_above_its_detail(dialog):
    """Measured on the painted box, not on the class list.

    The preview carries Bootstrap's `alert` class for its colours, and
    Tabler's `.alert` is `display: flex` — so its four children (the summary,
    the count pills, each heading and each list of keys) laid out **side by
    side in one row**, three narrow columns jammed against each other, while
    every assertion about the preview's *text* passed. That is this project's
    recurring browser defect in its usual shape: the content is in the DOM and
    the reader cannot use it.

    `getBoundingClientRect` rather than `getComputedStyle(...).display`, on the
    same reasoning `tests/admin/test_button_hint_browser.py` gives for
    measuring width rather than trusting `is_visible()`: a rule can be present
    and overridden, and what decides whether a person can read this is where
    the boxes actually are.
    """
    _drop(dialog, "sectors.csv", _csv("stack", "Stacked"), "text/csv")
    dialog.click("#kaicalc-import-check")
    dialog.wait_for_selector("#kaicalc-import-preview:visible", timeout=15000)

    boxes = dialog.evaluate(
        """() => {
            const p = document.getElementById('kaicalc-import-preview');
            const summary = p.querySelector('p');
            const rows = p.querySelector('.kaicalc-import-rows');
            return [summary.getBoundingClientRect().bottom,
                    rows.getBoundingClientRect().top];
        }"""
    )
    assert boxes[0] <= boxes[1] + 1, (
        "the preview's list of rows is drawn beside its summary rather than "
        f"below it; the summary ends at y={boxes[0]} and the list starts at "
        f"y={boxes[1]}"
    )
    assert _sectors() == {}


def test_nothing_is_written_until_the_import_button_is_pressed(dialog):
    """The confirmation, from both sides.

    Hidden before the preview, offered after it, and the row appears only once
    it is pressed. A dialog that wrote on the first press would pass every
    assertion about the preview's contents.
    """
    hidden = dialog.is_visible("#kaicalc-import-confirm")
    assert not hidden, (
        "the dialog offers to import before anything has been previewed"
    )

    _drop(dialog, "sectors.csv", _csv("confirm", "Confirmed"), "text/csv")
    dialog.click("#kaicalc-import-check")
    # `:visible` ON EACH ALTERNATIVE, AND THAT WAS MEASURED. Written as
    # `"#kaicalc-import-preview, #kaicalc-import-message"` this waits on
    # whichever element comes FIRST IN THE DOM — `querySelector` semantics —
    # so it watches the preview and never notices the message, and the
    # mutation above timed out instead of reaching the assertion below.
    dialog.wait_for_selector(
        "#kaicalc-import-preview:visible, #kaicalc-import-message:visible",
        timeout=15000,
    )
    assert _sectors() == {}, "the file was written by the check"
    dialog.wait_for_selector("#kaicalc-import-confirm", state="visible")

    dialog.click("#kaicalc-import-confirm")
    dialog.wait_for_selector("#kaicalc-import-message.alert-success", timeout=15000)

    assert _sectors() == {f"{_PREFIX}confirm": "Confirmed"}, (
        "the row did not land after the import was confirmed"
    )


def test_changing_the_file_spends_the_confirmation(dialog):
    """A preview describes one file in one mode.

    Leaving the button live after the file changed would be confirming
    something the visitor never saw — which, with upsert underneath, is how a
    file full of new rows gets written by somebody who previewed a file full
    of corrections.
    """
    _drop(dialog, "sectors.csv", _csv("first", "First"), "text/csv")
    dialog.click("#kaicalc-import-check")
    dialog.wait_for_selector("#kaicalc-import-confirm", state="visible")

    _drop(dialog, "other.csv", _csv("second", "Second"), "text/csv")

    assert not dialog.is_visible("#kaicalc-import-confirm"), (
        "the confirm button survived the file being changed under it"
    )
    assert not dialog.is_visible("#kaicalc-import-preview"), (
        "the preview of the previous file is still on screen beside the new "
        "file's name"
    )
    assert _sectors() == {}


def test_changing_the_mode_spends_the_confirmation(dialog):
    """The same, for the other half of what a preview describes.

    The second mode retires every row the file does not name. A confirmation
    carried over from a preview of the first mode would be a press that means
    something quite different from what was read.
    """
    _drop(dialog, "sectors.csv", _csv("mode", "Mode"), "text/csv")
    dialog.click("#kaicalc-import-check")
    dialog.wait_for_selector("#kaicalc-import-confirm", state="visible")

    dialog.select_option("#kaicalc-import-mode", "deactivate_missing")

    assert not dialog.is_visible("#kaicalc-import-confirm"), (
        "the confirm button survived the mode being changed under it"
    )
    assert _sectors() == {}


# --- 3. both formats, in the one dialog ------------------------------------


def test_a_json_file_goes_through_the_same_dialog(dialog):
    """Gap 3's browser half. One dialog, two formats, one flow.

    The dialog does not branch on the extension and neither does the route:
    the file is checked, previewed and confirmed exactly as a CSV is, and
    lands through the same upsert and the same audit trail.
    """
    _drop(dialog, "sectors.json", _json("json", "From JSON"), "application/json")
    assert dialog.inner_text("#kaicalc-import-filename").strip() == "sectors.json"

    dialog.click("#kaicalc-import-check")
    dialog.wait_for_selector("#kaicalc-import-confirm", state="visible")
    preview = dialog.inner_text("#kaicalc-import-preview")
    assert "1 row(s) would be added" in preview, preview
    assert "array" in preview, (
        "the preview of a JSON file says nothing about what its line numbers "
        f"count: {preview!r}"
    )
    assert _sectors() == {}

    dialog.click("#kaicalc-import-confirm")
    dialog.wait_for_selector("#kaicalc-import-message.alert-success", timeout=15000)
    assert _sectors() == {f"{_PREFIX}json": "From JSON"}


# --- 4. a refusal reaches the reader intact --------------------------------


def test_a_refusal_is_shown_with_its_line_breaks(dialog):
    """A refusal names **every** value it could not read, one per line.

    The browser's default `white-space: normal` collapses that into one
    run-on paragraph — the wall of text those messages were written to
    replace. Measured on the rendered element rather than grepped for in the
    stylesheet, because a rule that is in the page and not in effect looks
    identical to a test that reads the source.
    """
    body = (",".join(_COLUMNS)
            + f"\r\n{_PREFIX}bad1,Bad one,,1,5,1"
            + f"\r\n{_PREFIX}bad2,Bad two,,9e9,1\r\n")
    _drop(dialog, "sectors.csv", body, "text/csv")
    dialog.click("#kaicalc-import-check")
    dialog.wait_for_selector("#kaicalc-import-message.alert-danger", timeout=15000)

    assert dialog.evaluate(
        "() => getComputedStyle("
        "document.getElementById('kaicalc-import-message')).whiteSpace"
    ) in {"pre-wrap", "break-spaces"}, (
        "a multi-line refusal is rendered with the newlines collapsed, so a "
        "file with several bad values is shown as one paragraph"
    )
    text = dialog.inner_text("#kaicalc-import-message")
    assert text.count("\n") >= 2, (
        f"the refusal reached the reader as one line: {text!r}"
    )
    assert not dialog.is_visible("#kaicalc-import-confirm"), (
        "a file that was refused can still be imported"
    )
    assert _sectors() == {}
